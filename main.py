import os
import logging
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from aiohttp import ClientSession
from cachetools import TTLCache
from mi_session import MinaProvider
from routes import get_router

from miservice import MiAccount, MiNAService, MiIOService

from const import TTS_COMMAND


# 日志配置（从环境变量读取）
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FORMAT = os.getenv('LOG_FORMAT', '%(asctime)s | %(levelname)s | %(message)s')
LOG_DATE_FORMAT = os.getenv('LOG_DATE_FORMAT', '%H:%M:%S')

# 配置日志
LOGGER = logging.getLogger("xiaomi_api")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT
)

# 小米会话文件路径（从环境变量读取，默认使用临时目录）
MI_TOKEN_PATH = os.getenv('MI_SESSION_PATH', '/tmp/.mi_account_session.json')

# 从环境变量读取小米账号凭据
XIAOMI_USERNAME = os.getenv('XIAOMI_USERNAME', '')
XIAOMI_PASSWORD = os.getenv('XIAOMI_PASSWORD', '')

if not XIAOMI_USERNAME or not XIAOMI_PASSWORD:
    LOGGER.warning("环境变量 XIAOMI_USERNAME 或 XIAOMI_PASSWORD 未设置，将依赖会话文件恢复登录")

# API 服务配置（从环境变量读取）
HOST = os.getenv('API_HOST', '0.0.0.0')
PORT = int(os.getenv('API_PORT', '8000'))
DEBUG = os.getenv('API_DEBUG', 'false').lower() in ('true', '1', 'yes')

# 全局变量
http_session: Optional[ClientSession] = None
mi_account: Optional[MiAccount] = None
mina_service: Optional[MiNAService] = None
# 设备列表TTL缓存（仅用于 resolve_device_id 内部使用）
devices_ttl_cache: TTLCache = TTLCache(maxsize=1, ttl=30)
# Provider 实例（为路由模块提供）
mina_provider: MinaProvider | None = None


# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_session, mi_account, mina_service
    LOGGER.info("启动 FastAPI 应用...")

    # 创建 HTTP 会话
    http_session = ClientSession()
    LOGGER.info("HTTP 会话已创建")

    # 确保会话文件目录存在
    token_dir = os.path.dirname(MI_TOKEN_PATH)
    if token_dir:
        os.makedirs(token_dir, exist_ok=True)

    # 初始化小米 Provider 并尝试登录
    try:
        global mina_provider
        from mi_session import MinaProvider
        mina_provider = MinaProvider(MI_TOKEN_PATH, http_session)

        # 先尝试从会话文件恢复
        restored = await mina_provider.try_restore_from_file()

        if restored:
            LOGGER.info("已基于会话文件恢复小米会话")
        elif XIAOMI_USERNAME and XIAOMI_PASSWORD:
            # 如果会话恢复失败且环境变量已设置，则自动登录
            LOGGER.info("会话文件恢复失败，使用环境变量登录小米账号...")
            try:
                await mina_provider.login(XIAOMI_USERNAME, XIAOMI_PASSWORD)
                LOGGER.info("小米账号自动登录成功")
            except Exception as login_exc:
                LOGGER.error(f"小米账号自动登录失败: {login_exc}")
        else:
            LOGGER.warning("未能恢复会话且环境变量未设置，小米功能将不可用")

        # 启动会话文件监听
        try:
            await mina_provider.start_session_file_watcher()
        except Exception as _exc:
            LOGGER.warning(f"会话文件监听启动失败：{_exc}")
    except Exception as exc:
        LOGGER.warning(f"小米 Provider 初始化失败：{exc}")

    yield

    # 清理资源
    if http_session:
        await http_session.close()
        LOGGER.info("HTTP 会话已关闭")
    # 停止会话文件监听
    try:
        if mina_provider:
            await mina_provider.stop_session_file_watcher()
    except Exception:
        pass


# 应用信息配置（从环境变量读取）
APP_NAME = os.getenv('APP_NAME', '小米音响控制 API')
APP_VERSION = os.getenv('APP_VERSION', '1.0.0')
APP_DESCRIPTION = os.getenv('APP_DESCRIPTION', '基于 FastAPI 和 MiService 的小米音响控制接口')

# 创建 FastAPI 应用
app = FastAPI(title=APP_NAME, description=APP_DESCRIPTION, version=APP_VERSION, lifespan=lifespan)

"""全局缓存控制中间件，防止敏感 JSON 被缓存"""
@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response

# 统一异常处理：返回 {"detail": "..."}，并保持 4xx/5xx 状态码
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    LOGGER.exception(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# 依赖注入
async def get_http_session() -> ClientSession:
    if not http_session:
        raise HTTPException(status_code=500, detail="HTTP 会话未初始化")
    return http_session


async def get_mina_service() -> MiNAService:
    """获取 MiNAService"""
    if not mina_service:
        # 懒加载：若存在会话文件，尝试用其恢复一次
        try:
            await try_restore_session_from_file_only()
        except Exception as exc:
            LOGGER.warning(f"懒加载恢复失败：{exc}")
        if not mina_service:
            raise HTTPException(status_code=401, detail="请先登录小米账号")
    return mina_service


# 辅助函数
async def login_with_session_check(session: ClientSession, username: str, password: str) -> tuple[MiAccount, MiNAService, List[Dict[str, Any]]]:
    """
    使用会话文件优先验证；若验证失败则清空会话并用账号密码重登。
    """
    LOGGER.info("准备登录：检查会话目录与文件")
    token_dir = os.path.dirname(MI_TOKEN_PATH) or "."
    os.makedirs(token_dir, exist_ok=True)
    LOGGER.debug(f"会话文件路径: {MI_TOKEN_PATH} | 存在: {os.path.exists(MI_TOKEN_PATH)}")

    async def do_login() -> tuple[MiAccount, MiNAService, List[Dict[str, Any]]]:
        LOGGER.info("执行登录流程（可能复用会话）")
        account = MiAccount(session, username, password, MI_TOKEN_PATH)
        LOGGER.debug("调用 account.login('micoapi')")
        await account.login("micoapi")
        mina = MiNAService(account)
        LOGGER.debug("拉取设备列表进行校验")
        devices = await mina.device_list()
        LOGGER.info(f"设备数量: {len(devices) if devices is not None else 0}")
        return account, mina, devices or []

    # 情况一：已有 session，则先用其校验设备列表
    if os.path.exists(MI_TOKEN_PATH):
        LOGGER.info("检测到已有会话文件，验证其有效性…")
        try:
            account, mina, devices = await do_login()
            LOGGER.info("会话有效，继续使用现有会话")
            return account, mina, devices
        except Exception as exc:
            LOGGER.warning(f"会话验证失败，将清空并重登。原因: {exc}")
            try:
                os.remove(MI_TOKEN_PATH)
                LOGGER.debug("已删除无效会话文件")
            except FileNotFoundError:
                LOGGER.debug("会话文件已不存在，无需删除")
            account, mina, devices = await do_login()
            LOGGER.info("已通过账号密码重新登录并生成新会话")
            return account, mina, devices

    # 情况二：无 session，直接账号密码登录并写入 session
    LOGGER.info("未发现会话文件，执行首次登录…")
    account, mina, devices = await do_login()
    LOGGER.info("首次登录成功，会话已写入")
    return account, mina, devices


async def try_restore_session_from_file_only() -> bool:
    """仅使用现有会话文件尝试恢复登录，不进行账号密码重登。
    成功返回 True 并初始化全局状态；失败或不存在会话文件返回 False。
    """
    global http_session, mi_account, mina_service
    if http_session is None:
        LOGGER.debug("HTTP 会话未初始化，无法恢复会话")
        return False
    if not os.path.exists(MI_TOKEN_PATH):
        LOGGER.debug("会话文件不存在，无法恢复会话")
        return False

    try:
        LOGGER.info("检测到会话文件，尝试基于会话恢复，仅使用本地 token 不自动重登…")

        # 创建账号实例，但 **不** 提供用户名/密码，避免自动登录
        account = MiAccount(http_session, "", "", MI_TOKEN_PATH)

        # 手动加载本地 token
        token = await account.token_store.load_token()
        if not token:
            LOGGER.warning("会话文件为空或解析失败")
            return False
        account.token = token

        # 直接请求设备列表，rel==False 防止 Auth 失败时自动重登
        headers = {
            'User-Agent': 'MiHome/6.0.103 (com.xiaomi.mihome; build:6.0.103.1; iOS 14.4.0) Alamofire/6.0.103 MICO/iOSApp/appStore/6.0.103'
        }
        url = 'https://api2.mina.mi.com/admin/v2/device_list?master=0'
        resp = await account.mi_request('micoapi', url, None, headers, relogin=False)

        if resp and resp.get('code') == 0:
            mi_account = account
            mina_service = MiNAService(account)
            return True

        LOGGER.warning("会话文件验证失败（code!=0），需重新登录")
        return False

    except Exception as exc:
        LOGGER.warning(f"会话文件恢复失败，需重新登录：{exc}")
        mi_account = None
        mina_service = None
        # 不缓存设备列表
        return False


def find_device_by_selector(devices: List[Dict[str, Any]], selector: str) -> Optional[str]:
    """根据选择器查找设备ID"""
    if not selector:
        return devices[0].get("deviceID") if devices else None

    # 1) 已是 deviceID（形如 UUID，有横杠）
    if "-" in selector and any(d.get("deviceID") == selector for d in devices):
        return selector

    # 2) 是 miotDID（纯数字）
    if selector.isdigit():
        for d in devices:
            if str(d.get("miotDID")) == selector:
                return d.get("deviceID")

    # 3) 按别名/名称匹配
    for d in devices:
        if d.get("alias") == selector or d.get("name") == selector:
            return d.get("deviceID")

    # 兜底：第一台
    return devices[0].get("deviceID") if devices else None


async def resolve_device_id(selector: str, mina: MiNAService) -> str:
    """解析设备选择器为设备ID，如果失败则抛出异常"""
    devices = devices_ttl_cache.get("devices")
    if devices is None:
        devices = await mina.device_list() or []
        devices_ttl_cache["devices"] = devices
    device_id = find_device_by_selector(devices, selector)
    if not device_id:
        raise HTTPException(
            status_code=400,
            detail=f"未找到匹配的设备: {selector}。请先调用 /devices 查看可用设备"
        )

    return device_id


@app.get("/")
async def root():
    return {"detail": "小米音响控制 API 服务正在运行", "version": APP_VERSION}


@app.get("/health")
async def health_check():
    return {"detail": "服务健康"}


def _get_provider():
    global mina_provider
    if mina_provider is None:
        raise HTTPException(status_code=500, detail="小米 Provider 未初始化")
    return mina_provider

app.include_router(get_router(_get_provider))


"""/mi/* 路由已移至 routes_mi.py"""


"""/devices 与 playback 路由已移至 routes_mi.py"""


"""播放控制相关路由已移至 routes_mi.py"""

"""播放状态查询已移至 routes_mi.py"""

"""暂停播放路由已移至 routes_mi.py"""


# @app.post("/playback/stop", response_model=ApiResponse)
# async def stop_playback(
#     request: PlayControlRequest,
#     mina: MiNAService = Depends(get_mina_service)
# ):
#     """停止播放"""
#     try:
#         device_id = await resolve_device_id(request.device_selector, mina)

#         LOGGER.info(f"停止播放 | selector={request.device_selector} | device_id={device_id}")

#         result = await mina.ubus_request(
#             device_id,
#             "player_play_operation",
#             "mediaplayer",
#             {"action": "stop", "media": "app_ios"},
#         )

#         LOGGER.info(f"停止命令完成 | result={result}")

#         return ApiResponse(
#             success=True,
#             message="停止命令已发送",
#             data={"result": result, "device_id": device_id}
#         )

#     except Exception as e:
#         LOGGER.error(f"停止失败: {e}")
#         if isinstance(e, HTTPException):
#             raise e
#         raise HTTPException(status_code=500, detail=f"停止失败: {str(e)}")


"""恢复播放路由已移至 routes_mi.py"""

"""设置音量路由已移至 routes_mi.py"""

async def perform_tts(device_id: str, text: str, mina: MiNAService) -> Any:
    try:
        # 根据设备ID获取硬件型号
        devices = await mina.device_list() or []
        device_info = next((d for d in devices if d.get("deviceID") == device_id), None)
        hardware = device_info.get("hardware") if device_info else None

        # 有 tts command 且能获取到数字 did 时，优先通过 miio 指令说话
        if hardware and hardware in TTS_COMMAND:
            LOGGER.info(f"The device {device_id} , {hardware} In Custom TTS, Call MiIOService TTS.")
            tts_cmd = TTS_COMMAND[hardware]
            text_no_spaces = text.replace(" ", ",")  # miio 指令中不能包含空格

            # MiIOService 需要账号实例
            miio_service = MiIOService(mina.account)

            # 数值型 miotDID
            did = str(device_info.get("miotDID") or "") if device_info else ""
            if did.isdigit():
                try:
                    siid, aiid = [int(x) for x in tts_cmd.split("-")] if "-" in tts_cmd else (int(tts_cmd), 1)
                    LOGGER.info("Call MiIOService.miotspec.action siid=%s aiid=%s", siid, aiid)
                    result = await miio_service.miot_action(did, (siid, aiid), [text_no_spaces])
                    return result
                except Exception as e:
                    LOGGER.warning("MiIOService TTS 调用失败，将回退 MiNAService | %s", e)
            else:
                LOGGER.warning("设备未提供有效 miotDID，回退至 MiNAService TTS")
        else:
            LOGGER.debug("Call MiNAService TTS.")
            result = await mina.text_to_speech(device_id, text)
            return result
    except Exception as e:
        LOGGER.exception(f"Exception during TTS: {e}")
        raise

"""TTS 路由已移至 routes_mi.py"""


"""设备查找路由已移至 routes_mi.py"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="debug" if DEBUG else "info"
    )
