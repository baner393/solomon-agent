# 本地数字人对接接口（预览）

这份契约描述首轮本地实现；数字人尚未实际连接。服务默认loopback、单用户、单进程。客户端先用--demo验证流和取消，再切真实端点。

## 分工

Agent提供检索证据和唯一一次模型文本生成。数字人提供ASR、编辑/提交、句子切分、TTS、音频队列、播放、口型、日志与端到端计时。原始音频不传Agent或云端。

## 请求

POST /v1/respond，JSON：

```json
{
  "request_id": "voice-turn-1",
  "session_id": "interview-1",
  "mode": "voice",
  "message": "请根据资料帮助我准备面试回答",
  "source_ids": ["sample"]
}
```

mode为draft或voice。省略ID时服务生成；实际调用方应保留返回的ID。source_ids必须来自配置的来源ID；未指定则检索所有配置来源，空数组表示本轮不用来源。请求不能提供任意文件路径。

同会话同时只处理一个请求；重复request_id明确拒绝。本地单用户标识不等于账户认证，跨用户部署需要额外身份层。

## SSE

所有data带request_id和session_id。按event分发，不把SSE整体当回答文本：

- started：接受请求，包含simulation状态。
- evidence：items包含source_id/title/text/locator；这是实际用于本轮的短摘录，最多3段。不是逐句事实核验。
- text_delta：delta是新增文本，直接追加；不可把累计全文重复送TTS。
- completed：包含最终text、basis（mixed或model_generated）、claim_verification:false、titles及timings。retrieval_ms/context_ms/first_model_text_ms描述Agent阶段，first_text_ms/total_ms从接口请求计时；真实可听首音由数字人计时。
- cancelled：本轮取消，后续迟到文本丢弃。
- error：失败，保留原问题以便重试。

终态为completed、cancelled、error之一；服务不主动发送任何聊天渠道消息。先看实际response schema再接入，预览接口变更同步此文档。

## 生命周期

POST /v1/sessions返回新session_id。

POST /v1/requests/{request_id}/cancel取消活动生成；数字人同时停止播放、清TTS队列。停止音频与取消模型是两条路径，都要执行。

DELETE /v1/sessions/{session_id}归档会话，清除可用上下文并保留期限内记录；存在活动请求时先取消并等待终态。返回archived:true及new_session_id，已归档ID不可复用。

断开流连接时服务取消本轮生成。服务重启不承诺接着播放旧流；中断请求可依据原问题重试。

## 依据、隐私与延迟

最多3段短摘录、中文回答、默认不推断个人经历。模型提示词约束不等于事实审核；依据类别保守呈现，不让UI声称每句已核验。当前没有开启个人事实推断的开关。

数字人负责30天本地内容日志，不录音；Agent仅返回所需证据与阶段耗时。完整语音目标通常约1秒/P95不超过2秒，是测试目标而非当前达标声明。首文本毫秒数不能替代首个完整句、TTS首音频或播放器实际出声时间。
