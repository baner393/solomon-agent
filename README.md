# Solomon Agent

独立的知识辅助智能体：读取授权知识来源，生成有引用的回复草稿，并为实时语音数字人提供文本流与取消接口。

这是预览版。现有 Solomon 知识库与 Hermes 系统继续运行；本项目不依赖它们的进程或 CLI，也不自动发送消息。数字人继续负责录音、ASR、TTS、播放与口型。

## 当前搭建范围

- Markdown 知识源、独立增量索引与来源白名单。
- 独立会话、中文草稿和语音面试两种模式。
- PydanticAI 自定义 OpenAI-compatible 端点与流式回答。
- 本地 FastAPI SSE 接口、按请求取消及来源依据。
- 明确标注的离线演示，配套测试与学习练习。

当前运行时采用可替换适配器，首轮采用 PydanticAI。Pi 比较结果和限制见架构说明。真实 SenseNova 兼容性、数字人端到端首音延迟与原生 Windows 尚需单独验收；离线演示不证明它们达标。

## 在 WSL Ubuntu 开始

Python 3.11+，首轮开发验证使用 Python 3.14。源码、配置与运行数据分别定位。

```bash
unset GIT_DIR GIT_WORK_TREE
git clone https://github.com/baner393/solomon-agent.git
cd solomon-agent
PROJECT_DIR="$PWD"
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install -e ".[dev]"
cp config.example.toml config.local.toml
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" index
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" draft "智能体如何读取知识？" --demo
```

`--demo` 使用本地模拟生成器，不调用真实模型，输出用于验证检索、引用和流式链路。

真实运行时，在本地配置设置模型名、base_url，并通过 `SOLOMON_API_KEY` 环境变量提供密钥。不要把密钥写进源码或提交配置。详见 [安装与运行](docs/SETUP.md)。

## 文档

- [架构与责任边界](docs/ARCHITECTURE.md)
- [数字人接口契约](docs/INTERFACE.md)
- [安装与运行](docs/SETUP.md)
- [第一轮学习练习](docs/LEARNING.md)
- [首轮验证与下一步](docs/VALIDATION.md)
- [贡献与检查](CONTRIBUTING.md)
- [第三方组件](THIRD_PARTY_NOTICES.md)

## 延迟目标

未来完整语音链路希望通常约 1 秒、95% 请求在 2 秒内开始可听回复。模型首文本、首完整句、TTS 首音频和真正开始播放分别测量。当前先验证文本与协议，不承诺完整语音目标已达到。

MIT；个人知识、凭据、会话、草稿与索引不随仓库发布。
