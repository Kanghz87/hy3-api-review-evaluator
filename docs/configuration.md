# 配置说明

返回[项目首页](../README.md) · [实验复现指南](evaluation.md)

## 配置来源

配置通过进程环境变量或项目根目录的 `.env` 提供。已设置的进程环境变量优先于 `.env`；
配置加载不会用文件中的值覆盖现有环境变量。首次配置可复制 [.env.example](../.env.example)，
已有 `.env` 时保留原文件。

Key 仅在实际调用模型时必需。离线规则检查、保存结果校验和人工指标复算不需要 Key。

```dotenv
HY3_API_KEY=your_key_here
```

`.env`、`.env.*`（除 `.env.example`）、Streamlit secrets 和私有 token 账本均被 Git 忽略。
不得把凭据放进源码、公开配置、日志、截图或 Issue。

## 服务与模型

| 变量 | 默认值 | 约束与用途 |
| --- | --- | --- |
| `HY3_API_KEY` | 空 | 从环境变量或本地文件读取，不在诊断信息中回显 |
| `HY3_BASE_URL` | `https://tokenhub.tencentmaas.com/v1` | 仅接受不含用户名、密码的 HTTPS URL |
| `HY3_MODEL` | `hy3` | 只接受 `hy3`，不允许切换其他模型 |
| `HY3_TIMEOUT_SECONDS` | `90` | 单次请求超时，允许 1～300 秒 |
| `HY3_REASONING_EFFORT` | `high` | 可选 `no_think`、`low`、`high` |
| `HY3_MAX_RETRIES` | `0` | 固定为 0，禁用客户端自动重试 |

服务地址应保留默认值，或仅配置为已确认可信的 Hy3 服务。HTTPS 校验不能证明自定义服务
可信；Key 会发送到配置的服务地址。兼容客户端不代表使用其他供应商的模型。

## 输入与输出限制

| 变量 | 默认值 | 允许范围 |
| --- | ---: | ---: |
| `HY3_MAX_FILE_BYTES` | 2,000,000 | 1,024～10,000,000 字节 |
| `HY3_MAX_CONTAINER_NODES` | 200,000 | 1,000～1,000,000 节点 |
| `HY3_MAX_NESTING_DEPTH` | 100 | 10～200 层 |
| `HY3_MAX_MODEL_CHARS` | 120,000 | 4,000～500,000 字符 |
| `HY3_MAX_OUTPUT_TOKENS` | 16,000 | 256～32,000 token |

模型输入经过脱敏和长度限制。外部 `$ref` 不会被自动获取；需要完整引用上下文时，应在上传
前准备已脱敏、引用已本地化的文档。仓库的 `.streamlit/config.toml` 还把网页上传限制为
2 MB；解析器随后按 `HY3_MAX_FILE_BYTES` 再校验一次。提高大小上限会增加内存、延迟与模型
调用成本，如确需调整，应同时审查服务端上传上限。

## Token 预算

| 变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `HY3_TOTAL_TOKEN_BUDGET` | 850,000 | 本地累计调用上限，允许配置为 1,000～850,000 |
| `HY3_DEFAULT_RUN_TOKEN_BUDGET` | 150,000 | 单次运行默认上限，不得超过累计上限 |

在线实验还支持 `--run-token-budget` 指定本次额度。调用前按 UTF-8 prompt 字节数、最大输出
token 和消息余量进行保守预留；可能超限的请求会在发送前被拒绝。额度是停止条件，不是预付
费用或需要消耗完的目标。

账本位于 `results/private/token-ledger.json`，只记录用途、时间和用量，不保存 Key、prompt
或模型响应。实验脚本共享该账本；进程锁和持久化预约会防止两个本地进程同时调用时覆盖
对方的记录。超时、连接中断或无法解析用量的响应按预约上限保守计入，避免一次不确定请求被
误记为零；这可能高估本地用量，但不会因此突破项目上限。

实验中断后应保留账本，不应通过删除账本绕过预算。如果进程在请求完成前异常退出，未完成的
预约会继续占用额度并令系统安全拒绝后续调用。此时先到 TokenHub 控制台核对实际用量，再由
维护者人工检查私有账本；不要为了继续运行而直接删除整个账本。
单个工作副本的账本不能统计其他程序、其他副本或其他设备的调用，也不能替代云平台的账户
用量和账单控制。提供商账单口径可能与返回的 token 用量不同。

## 本地诊断

Windows PowerShell：

```powershell
.venv\Scripts\hy3-evaluate.exe check
.venv\Scripts\hy3-evaluate.exe audit-local datasets/specs/hard-15-mixed-security.yaml
```

macOS / Linux 使用 `.venv/bin/hy3-evaluate`。`check` 仅显示脱敏后的配置与 Key 是否存在，
不验证远端权限；`audit-local` 执行本地规则检查，不发送模型请求。

若自定义包镜像提示找不到依赖，可以仅为安装命令指定官方包源，而不修改全局 pip 配置：

```powershell
.venv\Scripts\python.exe -m pip install . --index-url https://pypi.org/simple
```

模型调用失败时，应用只显示清洗后的错误类别。排查时检查服务地址、Key 权限和预算配置，
不要把 Key 或完整 Authorization Header 粘贴到终端、日志或反馈中。
