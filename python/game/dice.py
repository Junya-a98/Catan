import random


def roll_two_dice(rng=None):
    rng = rng or random
    return rng.randint(1, 6), rng.randint(1, 6)


def roll_dice(rng=None):
    return sum(roll_two_dice(rng))
