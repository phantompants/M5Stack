"""Parked-vehicle Dino Dash game, ported from shared/DinoSprite.h."""

from dataclasses import dataclass
from typing import List


@dataclass
class Obstacle:
    x: float = 0.0
    width: int = 0
    height: int = 0
    cactus: bool = False


class DinoGame:
    AUTO = "AUTO"
    READY = "READY"
    PLAYING = "PLAYING"
    OVER = "OVER"

    def __init__(self, best: int = 0):
        self.state = self.AUTO
        self.moving = True
        self.new_best = False
        self.beat_best = False
        self.on_ground = True
        self.y = 0.0
        self.vy = 0.0
        self.score = 0
        self.best = max(0, min(65535, int(best)))
        self.last_ms = 0
        self.over_ms = 0
        self.start_ms = 0
        self.seed = 0x2545F491
        self.world_width = 160
        self.character = "TREX"
        self.obstacles: List[Obstacle] = [Obstacle() for _ in range(3)]

    def update(self, now_ms: int, speed_kmh: float, world_width: int) -> None:
        elapsed = max(0, now_ms - self.last_ms)
        self.last_ms = now_ms
        dt = min(elapsed, 100) / 1000.0
        self.world_width = max(1, world_width)
        if speed_kmh > 5.0:
            self.moving = True
        elif speed_kmh < 3.0:
            self.moving = False
        if self.moving:
            self.state = self.AUTO
            return
        if self.state == self.AUTO:
            self.state = self.READY
            self._reset()
        if self.state != self.PLAYING:
            return

        self.score = min(65535, max(0, (now_ms - self.start_ms) // 1000))
        speed = min(90.0, self.score * 0.7 + 32.0)
        previous_y = self.y
        floor = 0.0
        for obstacle in self.obstacles:
            obstacle.x -= speed * dt
            if obstacle.x + obstacle.width < 0:
                self._respawn(obstacle)
            overlap = (obstacle.x < 4 + 18 and
                       obstacle.x + obstacle.width > 4 + 6)
            if not overlap:
                continue
            if previous_y >= obstacle.height - 1:
                floor = max(floor, float(obstacle.height))
            else:
                self._crash(now_ms)
                return

        self.vy -= 220.0 * dt
        self.y += self.vy * dt
        self.on_ground = self.y <= floor
        if self.on_ground:
            self.y = floor
            self.vy = 0.0

    def press(self, now_ms: int) -> None:
        if self.state == self.READY:
            self._reset()
            self.state = self.PLAYING
            self.vy = 95.0
        elif self.state == self.PLAYING and self.on_ground:
            self.vy = 95.0
            self.on_ground = False
        elif self.state == self.OVER and now_ms - self.over_ms > 700:
            self._reset()
            self.state = self.PLAYING

    def take_new_best(self) -> bool:
        changed = self.new_best
        self.new_best = False
        return changed

    def toggle_character(self) -> None:
        self.character = "BRONTO" if self.character == "TREX" else "TREX"

    def status_text(self) -> str:
        if self.state == self.PLAYING:
            return "{}s  PB {}s".format(self.score, self.best)
        if self.state == self.OVER and self.beat_best:
            return "NEW PB {}s!".format(self.score)
        if self.state == self.OVER:
            return "GAME OVER {}s  PB {}s".format(self.score, self.best)
        if self.state == self.READY:
            return "SPACE: PLAY"
        return ""

    def _next_random(self) -> int:
        self.seed ^= (self.seed << 13) & 0xFFFFFFFF
        self.seed ^= self.seed >> 17
        self.seed ^= (self.seed << 5) & 0xFFFFFFFF
        self.seed &= 0xFFFFFFFF
        return self.seed

    def _respawn(self, obstacle: Obstacle) -> None:
        rightmost = float(self.world_width)
        for other in self.obstacles:
            rightmost = max(rightmost, other.x + other.width)
        obstacle.cactus = self._next_random() % 2 == 0
        obstacle.width = 6 if obstacle.cactus else 7
        obstacle.height = (9 if obstacle.cactus else
                           4 + self._next_random() % 3)
        obstacle.x = rightmost + 50 + self._next_random() % 50

    def _crash(self, now_ms: int) -> None:
        self.state = self.OVER
        self.over_ms = now_ms
        self.beat_best = self.score > self.best
        if self.beat_best:
            self.best = self.score
            self.new_best = True

    def _reset(self) -> None:
        self.seed ^= self.last_ms | 1
        self.y = 0.0
        self.vy = 0.0
        self.on_ground = True
        self.score = 0
        self.start_ms = self.last_ms
        self.beat_best = False
        for obstacle in self.obstacles:
            obstacle.x = -100.0
        for obstacle in self.obstacles:
            self._respawn(obstacle)
