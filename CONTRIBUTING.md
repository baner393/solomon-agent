# 开发与检查

功能分支使用 codex/ 前缀，main 保留经过检查的提交历史。生产知识库、凭据与运行数据不进入仓库；测试使用临时目录和脱敏样本。

```bash
unset GIT_DIR GIT_WORK_TREE
PROJECT_DIR="$PWD"
"$PROJECT_DIR/.venv/bin/python" -m pip install -e ".[dev]"
"$PROJECT_DIR/.venv/bin/ruff" check .
"$PROJECT_DIR/.venv/bin/python" -m pytest -q
"$PROJECT_DIR/.venv/bin/python" -m build
```

涉及知识访问的修改须验证正常/失败路径不写来源目录；涉及响应生命周期的修改须验证取消、同会话并发、新会话和终态。真实性能需要注明模型、端点、环境、样本与统计区间。

数字人接口修改先更新契约，保持 request_id、增量文本与终态语义。第三方源码抽取须记录来源版本并保留许可证。当前不开发自动发消息、媒体入库或旧系统迁移。

自动检查模板位于docs/ci-workflow.example.yml，目前尚未启用。当前GitHub凭据缺少workflow权限；获具备权限的凭据后，可将模板放入.github/workflows/ci.yml。现阶段执行以上本地检查。
