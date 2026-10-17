"""Fake恢复测试的显式时间输入；实际进程实验不导入本模块。"""
from datetime import datetime, timezone
from unittest.mock import patch


class FixedRecoveryClock(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = datetime(2026, 10, 18, tzinfo=timezone.utc)
        return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)


def install_fixed_recovery_clock(test_case):
    # 持久deadline和异常时钟策略由test_recovery_timing的显式时间序列单独覆盖。
    for target in ("agentd.app.models.datetime", "agentd.app.runner.datetime", "agentd.app.runtime.execution.datetime"):
        clock = patch(target, FixedRecoveryClock)
        clock.start()
        test_case.addCleanup(clock.stop)
