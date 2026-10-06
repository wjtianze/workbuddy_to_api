# WB Gateway

Python 多账号模型网关，支持国内 / 国际账号、WebUI、余额与签到、模型倍率、调用记录和分区 API Key。不依赖原版桌面客户端或 Node.js 后端。

非官方项目，仅用于你有权使用的账号与接口。   

<img width="2538" height="1400" alt="02feabeb-ff93-44d6-8376-3c346d1cc2c9" src="https://github.com/user-attachments/assets/52ecf49a-479d-4a4d-a514-0d025919098a" />

一键运行，可视化web管理界面



## 运行

已在 Windows + Python 3.12 验证：

```powershell
py -3.12 -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe app.py
```

默认端口为 `8787`。在图形窗口打开控制台，复制管理密钥后登录。也可使用 `启动网关.pyw`。

无图形模式：

```powershell
./.venv/Scripts/python.exe app.py --headless --host 127.0.0.1 --port 8787
```

headless 模式的管理密钥可通过 `WB_ADMIN_KEY` 环境变量指定，未指定则自动生成并保存于私有 `data/keys.json`。其他系统需使用 headless 模式，本次未做其他系统实机验证。

## 接入

- 控制台：`http://127.0.0.1:8787/ui/`
- OpenAI 风格地址：`http://127.0.0.1:8787/v1`
- Messages 风格地址：`http://127.0.0.1:8787`

在控制台添加账号、完成官方网页授权，再创建调用 Key。客户端使用调用 Key，不使用管理密钥。

通过 `GET /v1/models` 获取实际模型，保留 `cn/` 或 `gl/` 前缀。思考强度、输出上限和工具由客户端设置，并遵循模型实际能力。支持 Chat Completions、无状态 Responses、Messages 和 SSE。

## 系统提示词

网关不自动添加默认系统提示词、身份设定或 JSON 输出提示。国内与国际账号都仅使用客户端提供的指令；`WB_DEFAULT_SYSTEM_PROMPT` 不再注入请求。

- Chat Completions 的 `system` / `developer`、Responses 的 `instructions` 和 Messages 的 `system` 会保留，显式空字符串也会保留。
- 上游不支持 `developer` 角色，因此将其内容接在客户端的 `system` 内容之后，合并为一条 `system` 消息，只用换行分隔，不追加说明文字。
- JSON / JSON Schema 要求通过原生 `response_format` 转发，返回结果仍经过现有 JSON 校验；不通过额外系统消息模拟格式要求。
- 工具定义、工具选择和思考参数沿用现有协议转换。此行为仅保证网关发出的请求不包含额外系统提示词，不能证明官方服务端没有自己的提示词。

## 注意

- 默认仅监听本机；远程部署需自行配置 HTTPS 与访问控制。
- 工具由客户端执行；不支持的能力及损坏参数会明确拒绝，不保证所有 Agent 的复杂工作流。

许可证尚未指定，发布前请自行选择并补充 `LICENSE`。
