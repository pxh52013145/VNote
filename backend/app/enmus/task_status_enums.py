import enum


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    SUMMARIZING = "SUMMARIZING"
    FORMATTING = "FORMATTING"
    SAVING = "SAVING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"

    @classmethod
    def description(cls, status):
        desc_map = {
            cls.PENDING: "排队中",
            cls.PARSING: "解析链接",
            cls.DOWNLOADING: "下载中",
            cls.TRANSCRIBING: "转录中",
            cls.SUMMARIZING: "总结中",
            cls.FORMATTING: "格式化中",
            cls.SAVING: "保存中",
            cls.SUCCESS: "完成",
            cls.FAILED: "失败",
        }
        return desc_map.get(status, "未知状态")

    @classmethod
    def progress(cls, status) -> int:
        """
        Return an estimated 0-100 progress value for the given status.

        This is a stage-based heuristic (not real-time byte/segment progress),
        but provides a stable user-facing progress bar during long tasks.
        """
        try:
            normalized = status if isinstance(status, cls) else cls(str(status))
        except Exception:
            return 0

        progress_map = {
            cls.PENDING: 0,
            cls.PARSING: 5,
            cls.DOWNLOADING: 20,
            cls.TRANSCRIBING: 55,
            cls.SUMMARIZING: 85,
            cls.FORMATTING: 92,
            cls.SAVING: 97,
            cls.SUCCESS: 100,
            cls.FAILED: 0,
        }
        return int(progress_map.get(normalized, 0))
