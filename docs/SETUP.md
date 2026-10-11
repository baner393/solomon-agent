# 安装、运行与路径

首轮支持 WSL Ubuntu，本地单用户服务。Python 3.11+；当前依赖与测试在3.14验证。原生Windows和数字人连接尚未验收。

## 安装

```bash
unset GIT_DIR GIT_WORK_TREE
PROJECT_DIR="$PWD"
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install -e ".[dev]"
cp "$PROJECT_DIR/config.example.toml" "$PROJECT_DIR/config.local.toml"
```

从仓库目录执行，或将PROJECT_DIR设为实际安装的绝对路径。没有用户名、盘符或profile home的固定要求。

## 配置

config.local.toml不提交。字段model、base_url、auth_mode、api_key_env、data_dir，max_output_tokens（默认512）、history_char_budget（默认6000）、可选reasoning_effort（默认不发送），以及[sources]下的来源ID和目录。所有相对路径从配置文件所在目录解析，不从启动工作目录解析。

环境变量SOLOMON_MODEL、SOLOMON_BASE_URL、SOLOMON_DATA_DIR、SOLOMON_API_KEY、SOLOMON_AUTH_MODE覆盖对应配置。默认auth_mode为api_key，密钥由环境提供，不写配置或日志。已明确无需认证的服务可显式设auth_mode为none，必须提供model与合法base_url，且不会读取或转发密钥环境变量。doctor仅显示是否配置密钥，不显示值。

现有vault初测时在本地配置增加其绝对路径，保留data_dir在新项目独立位置。先使用脱敏样例，确认正常/失败路径无来源写入后，再授权读取真实知识。只读知识接入不运行旧Solomon init、入库或Hermes命令。

## 索引与离线演示

```bash
unset GIT_DIR GIT_WORK_TREE
PROJECT_DIR="$PWD"
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" doctor
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" index
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" draft "智能体如何读取知识？" --demo
```

索引显式构建/增量更新，服务启动和每轮检索不扫描整个vault。资料变化后重新执行index。默认跳过隐藏目录与隐藏文件（包括.git/.kb/.obsidian/.trash），只索引非隐藏.md。source和data_dir重叠、越界symlink、不可写数据目录明确失败，不回退到vault/.kb。

## 本地接口

```bash
unset GIT_DIR GIT_WORK_TREE
PROJECT_DIR="$PWD"
"$PROJECT_DIR/.venv/bin/solomon-agent" --config "$PROJECT_DIR/config.local.toml" serve --demo --port 8765
```

默认只监听127.0.0.1。端口可选，不抢占旧服务。访问/health确认simulation字段；去掉--demo前配置真实模型与密钥。接口见INTERFACE.md。

此版本没有多租户认证，不应把它直接暴露公网。跨Windows/WSL连接由部署拓扑和主机配置决定；不能默认两个localhost是同一服务。需要跨环境监听、鉴权或网络配置时先单独设计。

## 错误与数据

模型未配置时真实模式失败，不静默转模拟。来源缺失/未启用、不可写数据目录、模型失败等不修改旧知识库；用户问题可在调用方保留后重试。

独立data_dir保存知识索引、中文分词缓存和会话。voice模式历史保留30天；过期清理在启动与访问时执行，当前没有常驻独立清理定时器。draft模式当前默认保留，删除或设定期限需要明确策略。完整数字人30天日志由数字人项目统一管理。

当前按一个服务进程管理会话。不要多个worker共享同一运行目录；不使用uvicorn多worker。重启中断的模型流不能原样续跑，可根据保留问题重新发起请求。
