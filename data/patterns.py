# Detection patterns for various device types

# Known beacon prefixes for tracker detection
AIRTAG_PREFIXES = ["4C:00"]  # Apple continuity
TILE_PREFIXES = ["C4:E7", "DC:54", "E4:B0", "F8:8A"]
SAMSUNG_TRACKER = ["58:4D", "A0:75"]

# Drone detection patterns (SSID patterns)
DRONE_SSID_PATTERNS = [
    # DJI
    "DJI-",
    "DJI_",
    "Mavic",
    "Phantom",
    "Spark-",
    "Mini-",
    "Air-",
    "Inspire",
    "Matrice",
    "Avata",
    "FPV-",
    "Osmo",
    "RoboMaster",
    "Tello",
    # Parrot
    "Parrot",
    "Bebop",
    "Anafi",
    "Disco-",
    "Mambo",
    "Swing",
    # Autel
    "Autel",
    "EVO-",
    "Dragonfish",
    "Lite+",
    "Nano",
    # Skydio
    "Skydio",
    # Other brands
    "Holy Stone",
    "Potensic",
    "SYMA",
    "Hubsan",
    "Eachine",
    "FIMI",
    "Xiaomi_FIMI",
    "Yuneec",
    "Typhoon",
    "PowerVision",
    "PowerEgg",
    # Generic drone patterns
    "Drone",
    "UAV-",
    "Quadcopter",
    "FPV_",
    "RC-Drone",
]

# Drone OUI prefixes (MAC address prefixes for drone manufacturers)
DRONE_OUI_PREFIXES = {
    # DJI
    "60:60:1F": "DJI",
    "48:1C:B9": "DJI",
    "34:D2:62": "DJI",
    "E0:DB:55": "DJI",
    "C8:6C:87": "DJI",
    "A0:14:3D": "DJI",
    "70:D7:11": "DJI",
    "98:3A:56": "DJI",
    # Parrot
    "90:03:B7": "Parrot",
    "A0:14:3D": "Parrot",
    "00:12:1C": "Parrot",
    "00:26:7E": "Parrot",
    # Autel
    "8C:F5:A3": "Autel",
    "D8:E0:E1": "Autel",
    # Yuneec
    "60:60:1F": "Yuneec",
    # Skydio
    "F8:0F:6F": "Skydio",
}
