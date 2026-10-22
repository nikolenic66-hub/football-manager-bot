"""Three-channel structural matchup helpers."""

CHANNELS = ('LEFT','CENTER','RIGHT')


def _channel(x):
    if x < 33.34:
        return 'LEFT'
    if x > 66.66:
        return 'RIGHT'
    return 'CENTER'


def _slot_channel(slot):
    if slot in {'LB','LWB','LM','LW'}:
        return 'LEFT'
    if slot in {'RB','RWB','RM','RW'}:
        return 'RIGHT'
    return 'CENTER'


def count_channel_players(board, channel):
    channel = channel.upper()
    if channel not in CHANNELS:
        raise ValueError(f'Unsupported channel: {channel}')
    return sum(1 for slot in board.slots if _slot_channel(slot.slot) == channel)


def channel_matchup(own_board, opp_board):
    """Return relative channel control; >1 means own side has more bodies."""
    result = {}
    for channel in CHANNELS:
        own = count_channel_players(own_board, channel)
        opp = count_channel_players(opp_board, channel)
        result[channel] = round((own + 1.0) / (opp + 1.0), 4)
    return result
