from control._def import CHANNEL_COLORS_MAP


def extract_wavelength_from_config_name(name):
    # Split the string and find the wavelength number immediately after "Fluorescence"
    parts = name.split()
    if "Fluorescence" in parts:
        index = parts.index("Fluorescence") + 1
        if index < len(parts):
            return parts[index].split()[0]  # Assuming 'Fluorescence 488 nm Ex' and taking '488'
    for color in ["R", "G", "B"]:
        if color in parts or "full_" + color in parts:
            return color
    return None


def get_channel_color(channel):
    channel_info = CHANNEL_COLORS_MAP.get(
        extract_wavelength_from_config_name(channel), {"hex": 0xFFFFFF, "name": "gray"}
    )
    return channel_info["hex"]
