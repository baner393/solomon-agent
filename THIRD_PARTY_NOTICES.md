# 第三方组件与来源

本项目通过依赖复用 PydanticAI、FastAPI、Uvicorn、jieba 及 Python SQLite，不将这些组件的源码复制到仓库。各组件保留自身许可证；构建和分发前以实际锁定版本的元数据核对。

- PydanticAI：https://github.com/pydantic/pydantic-ai
- FastAPI：https://github.com/fastapi/fastapi
- Uvicorn：https://github.com/encode/uvicorn
- jieba：https://github.com/fxsjy/jieba
- Pi（选型候选，尚非运行依赖）：https://github.com/earendil-works/pi

现有 Solomon 的只读知识接入经验和确定性检索设计作为参考，当前不导入旧仓库历史、生产配置或媒体管线。后续如抽取 Hermes 或其他项目源码，须在提交中记录来源版本与文件，并附原版权和许可声明。
