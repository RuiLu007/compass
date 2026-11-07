# 可靠性模拟演示

平台管理员通过“Agent 与模型”页面运行固定场景。`provider.py` 仅模拟供应商 HTTP 行为；`service.py` 调用生产 Runtime、SDK Adapter、输出校验和 ReliableExecutor。每次运行隔离 Gate，最多 20 条调用、20 秒，不使用真实密钥、不访问网络、不写业务表。

场景与预期集中维护于 `service.py:CATALOG`。不能将模拟结果解释为厂商可用性或生产吞吐。断连取消所有所属任务；外层 API 每进程只接纳一个演示。测试：`pnpm test -F agent -- tests/test_reliability_showcase.py`。
