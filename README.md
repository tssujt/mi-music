# 小米音响控制 API

基于 FastAPI 和 MiService 的小米音响控制接口。

## 使用流程

1. 设置环境变量 `XIAOMI_USERNAME` 和 `XIAOMI_PASSWORD`
2. 启动服务，系统会自动登录小米账号
3. 直接使用设备与播放控制接口（无需认证）

## 兼容提醒

我只有一台小米音响Play增强版(L05C)，所以只测试了该设备, 其他设备未测试, 期待你的反馈!

## 功能特性

- 🎵 播放控制（播放URL、暂停、恢复）
- 🔊 音量控制
- 🗣️ 文字转语音
- 📱 设备管理
- 🔄 自动登录（基于环境变量）
- 💾 会话持久化

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

设置必需的环境变量：

```bash
# 必需：小米账号凭据
export XIAOMI_USERNAME="your_xiaomi_account@example.com"
export XIAOMI_PASSWORD="your_password"

# 可选：API 服务配置
export API_HOST="0.0.0.0"
export API_PORT="8000"
export API_DEBUG="false"

# 可选：日志配置
export LOG_LEVEL="INFO"

# 可选：会话文件路径（默认 /tmp/.mi_account_session.json）
export MI_SESSION_PATH="/path/to/session.json"
```

或创建 `.env` 文件（需配合 python-dotenv 或 Docker --env-file）：

```bash
XIAOMI_USERNAME=your_xiaomi_account@example.com
XIAOMI_PASSWORD=your_password
API_PORT=8000
LOG_LEVEL=INFO
```

### 3. 启动服务

```bash
# 确保已设置环境变量后启动
python main.py

# 或
uvicorn main:app --reload
```

### 4. 访问 API

- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

### 5. 使用 Docker 运行

```bash
# 1) 构建镜像
docker build -t music-mi:latest .

# 2) 运行容器（传递环境变量）
docker run -d --name music-mi \
  -p 8000:8000 \
  -e XIAOMI_USERNAME="your_account@example.com" \
  -e XIAOMI_PASSWORD="your_password" \
  music-mi:latest

# 或使用 .env 文件
docker run -d --name music-mi \
  -p 8000:8000 \
  --env-file .env \
  music-mi:latest

# 可选：挂载会话文件以持久化登录状态
docker run -d --name music-mi \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/session.json:/tmp/.mi_account_session.json \
  music-mi:latest

# 3) 健康检查
curl http://localhost:8000/health
```

提示：
- 环境变量 `XIAOMI_USERNAME` 和 `XIAOMI_PASSWORD` 为必需
- 默认会话文件保存在容器内 `/tmp/.mi_account_session.json`，可通过 `MI_SESSION_PATH` 环境变量自定义
- 挂载会话文件可实现重启后保持登录状态
- 默认容器监听 0.0.0.0:8000，生产环境建议置于反向代理之后，并开启 TLS

### 主要接口

- 设备管理
  - `GET /devices` - 获取设备列表
  - `POST /mi/device/playback/play-url` - 播放 URL
  - `POST /mi/device/playback/pause` - 暂停播放
  - `POST /mi/device/playback/play` - 恢复播放
  - `POST /mi/device/playback/stop` - 停止播放
  - `GET /mi/device/playback/status` - 播放状态
  - `POST /mi/device/volume` - 设置音量
  - `GET /mi/device/volume` - 获取音量
  - `POST /mi/device/tts` - 文字转语音

## 环境变量说明

### 必需变量
- `XIAOMI_USERNAME`: 小米账号（邮箱或手机号）
- `XIAOMI_PASSWORD`: 小米账号密码

### 可选变量
- `MI_SESSION_PATH`: 会话文件路径，默认 `/tmp/.mi_account_session.json`
- `API_HOST`: API 监听地址，默认 `0.0.0.0`
- `API_PORT`: API 监听端口，默认 `8000`
- `API_DEBUG`: 调试模式，默认 `false`
- `LOG_LEVEL`: 日志级别，默认 `INFO`
- `LOG_FORMAT`: 日志格式
- `LOG_DATE_FORMAT`: 日志时间格式

## 备注

- 应用启动时会自动使用环境变量中的小米账号登录
- 会话文件默认保存在 `/tmp/.mi_account_session.json`，支持会话持久化和热加载
- 如果环境变量未设置，系统会尝试从会话文件恢复登录状态
- 所有 API 端点无需认证，请注意网络安全（建议使用反向代理限制访问）

## 许可证

MIT License

## Credits

- [miservice](https://github.com/Yonsm/MiService)
- [fastapi](https://github.com/fastapi/fastapi)
- [jwt](https://github.com/jpadilla/pyjwt)
