"""Official CustomController for saved-image offline replay only; no device I/O."""
from maa.controller import CustomController


class SavedImages(CustomController):
    def __init__(self, stages):
        super().__init__()
        self.stages = stages
        self.stage = 0
        self.frame = 0
        self.clicks = []
        self.swipes = []

    def connect(self):
        return True

    def request_uuid(self):
        return "offline-saved-image-replay"

    def get_features(self):
        return 0

    def screencap(self):
        frames = self.stages[self.stage]
        image = frames[min(self.frame, len(frames) - 1)]
        self.frame += 1
        return image.copy()

    def click(self, x, y):
        self.clicks.append([x, y])
        self.stage = min(self.stage + 1, len(self.stages) - 1)
        self.frame = 0
        return True

    def swipe(self, x1, y1, x2, y2, duration):
        self.swipes.append([x1, y1, x2, y2, duration])
        self.stage = min(self.stage + 1, len(self.stages) - 1)
        self.frame = 0
        return True

    def denied(self, *args):
        return False

    start_app = stop_app = touch_down = touch_move = touch_up = denied
    click_key = input_text = key_down = key_up = denied
