from datetime import datetime

class TimeUtils:
    @staticmethod
    def get_timestamp() -> str:
        return datetime.now().strftime('%Y%m%d_%H%M%S')
    
    @staticmethod
    def get_current_time() -> datetime:
        return datetime.now()
