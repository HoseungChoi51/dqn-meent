"""Schedules use their frozen original duration, including after extensions."""


def epsilon_at_step(step: int, config) -> float:
    duration = max(1, round(config.total_steps * config.exploration_fraction))
    fraction = min(max(step, 0) / duration, 1.0)
    if config.epsilon_schedule == "paper":
        return max(config.epsilon_end, config.epsilon_start * (1 - fraction))
    return config.epsilon_start + fraction * (config.epsilon_end - config.epsilon_start)
