"""
DSC (Digital Selective Calling) constants per ITU-R M.493.

This module contains all DSC-specific constants including format codes,
distress nature codes, telecommand definitions, and MID (Maritime
Identification Digits) country mappings.
"""

from __future__ import annotations

# =============================================================================
# DSC Format Specifier Codes
# Per ITU-R M.493-16 Table 1
# =============================================================================

FORMAT_CODES = {
    100: "SELECTIVE_CALL",          # Selective call (to individual station)
    102: "GEOGRAPHICAL_AREA",       # Geographical area call
    112: "DISTRESS",                # Distress alert
    113: "INDIVIDUAL_ACK",          # Individual acknowledgement
    114: "GROUP",                   # Group call
    115: "SHIP_POSITION",           # Ship position request / response
    116: "ALL_SHIPS",               # All ships call
    118: "DISTRESS_RELAY",          # Distress relay
    120: "INDIVIDUAL",              # Individual call
    121: "MEDICAL_TRANSPORT",       # Medical transport
    123: "AUTOMATIC_SERVICE",       # Automatic / selective service
}

# Valid ITU-R M.493 format specifiers
VALID_FORMAT_SPECIFIERS = {100, 102, 112, 113, 114, 115, 116, 118, 120, 121, 123}

# Valid EOS (End of Sequence) symbols per ITU-R M.493
VALID_EOS = {117, 122, 127}

# Category priority (lower = higher priority)
CATEGORY_PRIORITY = {
    "DISTRESS": 0,
    "URGENCY": 2,
    "SAFETY": 3,
    "ALL_SHIPS": 4,
    "SHIPS_BUSINESS": 5,
    "ROUTINE": 6,
    "GEOGRAPHICAL_AREA": 6,
    "GROUP": 6,
    "INDIVIDUAL": 6,
    "INDIVIDUAL_ACK": 6,
    "SELECTIVE_CALL": 6,
    "AUTOMATIC_SERVICE": 6,
    "DISTRESS_RELAY": 1,
    "MEDICAL_TRANSPORT": 2,
    "SHIP_POSITION": 6,
}

# =============================================================================
# Nature of Distress Codes
# Per ITU-R M.493-16 Table 3
# Symbol value encodes nature in the lower 5 bits (0-31)
# =============================================================================

DISTRESS_NATURE_CODES = {
    0: "FIRE_EXPLOSION",
    1: "FLOODING",
    2: "COLLISION",
    3: "GROUNDING",
    4: "LISTING_DANGER_CAPSIZING",
    5: "SINKING",
    6: "DISABLED_ADRIFT",
    7: "UNDESIGNATED_DISTRESS",
    8: "ABANDONING_SHIP",
    9: "PIRACY_ARMED_ROBBERY",
    10: "MAN_OVERBOARD",
    11: "EPIRB_EMISSION",
    12: "UNALLOCATED_12",
    13: "UNALLOCATED_13",
    14: "UNALLOCATED_14",
    15: "UNALLOCATED_15",
    16: "UNALLOCATED_16",
    17: "UNALLOCATED_17",
    18: "UNALLOCATED_18",
    19: "UNALLOCATED_19",
    20: "UNALLOCATED_20",
    21: "UNALLOCATED_21",
    22: "UNALLOCATED_22",
    23: "UNALLOCATED_23",
    24: "UNALLOCATED_24",
    25: "UNALLOCATED_25",
    26: "TEST_TRAINING",
    27: "UNALLOCATED_27",
    28: "UNALLOCATED_28",
    29: "UNALLOCATED_29",
    30: "UNALLOCATED_30",
    31: "NO_INFORMATION",
}

# =============================================================================
# Telecommand Codes (First and Second)
# Per ITU-R M.493-16 Tables 4-5
# =============================================================================

TELECOMMAND_CODES = {
    # First telecommand (type of subsequent communication)
    100: "F3E_G3E_ALL",
    101: "F3E_G3E_DUPLEX",
    102: "POLLING",
    103: "UNABLE_TO_COMPLY",
    104: "END_OF_CALL",
    105: "DATA",
    106: "J3E_TELEPHONY",
    107: "DISTRESS_ACK",
    108: "DISTRESS_RELAY",
    109: "F1B_J2B_FEC",
    110: "F1B_J2B_ARQ",
    111: "TEST",
    112: "SHIP_POSITION",
    113: "NO_INFO",
    118: "FREQ_ANNOUNCEMENT",
    126: "NO_REASON",
    200: "F3E_G3E_SIMPLEX",
    201: "POLL_RESPONSE",
}

# Full 0-127 telecommand lookup
TELECOMMAND_CODES_FULL = {i: TELECOMMAND_CODES.get(i, f"UNKNOWN_{i}") for i in range(128)}

# Format codes that carry telecommand fields
TELECOMMAND_FORMATS = {102, 112, 114, 116, 120, 123, 113, 115, 118, 121}

# Minimum symbols (after phasing strip) before an EOS can be accepted
MIN_SYMBOLS_FOR_FORMAT = 12

# =============================================================================
# DSC Symbol Definitions
# Per ITU-R M.493-16
# =============================================================================

# Phasing symbols
DSC_SYMBOLS = {
    120: "DX",  # Dot pattern / phasing
    121: "RX",
    122: "SX",
    123: "S0",
    124: "S1",
    125: "S2",
    126: "S3",
    127: "EOS",
}

# =============================================================================
# MID (Maritime Identification Digits) Country Mapping
# First 3 digits of MMSI identify the country
# Per ITU MID table
# =============================================================================

MID_COUNTRY_MAP = {
    # Americas
    "201": "Albania",
    "202": "Andorra",
    "203": "Austria",
    "204": "Azores",
    "205": "Belgium",
    "206": "Belarus",
    "207": "Bulgaria",
    "208": "Vatican City",
    "209": "Cyprus",
    "210": "Cyprus",
    "211": "Germany",
    "212": "Cyprus",
    "213": "Georgia",
    "214": "Moldova",
    "215": "Malta",
    "216": "Armenia",
    "218": "Germany",
    "219": "Denmark",
    "220": "Denmark",
    "224": "Spain",
    "225": "Spain",
    "226": "France",
    "227": "France",
    "228": "France",
    "229": "Malta",
    "230": "Finland",
    "231": "Faroe Islands",
    "232": "United Kingdom",
    "233": "United Kingdom",
    "234": "United Kingdom",
    "235": "United Kingdom",
    "236": "Gibraltar",
    "237": "Greece",
    "238": "Croatia",
    "239": "Greece",
    "240": "Greece",
    "241": "Greece",
    "242": "Morocco",
    "243": "Hungary",
    "244": "Netherlands",
    "245": "Netherlands",
    "246": "Netherlands",
    "247": "Italy",
    "248": "Malta",
    "249": "Malta",
    "250": "Ireland",
    "251": "Iceland",
    "252": "Liechtenstein",
    "253": "Luxembourg",
    "254": "Monaco",
    "255": "Madeira",
    "256": "Malta",
    "257": "Norway",
    "258": "Norway",
    "259": "Norway",
    "261": "Poland",
    "262": "Montenegro",
    "263": "Portugal",
    "264": "Romania",
    "265": "Sweden",
    "266": "Sweden",
    "267": "Slovakia",
    "268": "San Marino",
    "269": "Switzerland",
    "270": "Czech Republic",
    "271": "Turkey",
    "272": "Ukraine",
    "273": "Russia",
    "274": "North Macedonia",
    "275": "Latvia",
    "276": "Estonia",
    "277": "Lithuania",
    "278": "Slovenia",
    "279": "Serbia",
    # North America
    "301": "Anguilla",
    "303": "USA",
    "304": "Antigua and Barbuda",
    "305": "Antigua and Barbuda",
    "306": "Curacao",
    "307": "Aruba",
    "308": "Bahamas",
    "309": "Bahamas",
    "310": "Bermuda",
    "311": "Bahamas",
    "312": "Belize",
    "314": "Barbados",
    "316": "Canada",
    "319": "Cayman Islands",
    "321": "Costa Rica",
    "323": "Cuba",
    "325": "Dominica",
    "327": "Dominican Republic",
    "329": "Guadeloupe",
    "330": "Grenada",
    "331": "Greenland",
    "332": "Guatemala",
    "334": "Honduras",
    "336": "Haiti",
    "338": "USA",
    "339": "Jamaica",
    "341": "Saint Kitts and Nevis",
    "343": "Saint Lucia",
    "345": "Mexico",
    "347": "Martinique",
    "348": "Montserrat",
    "350": "Nicaragua",
    "351": "Panama",
    "352": "Panama",
    "353": "Panama",
    "354": "Panama",
    "355": "Panama",
    "356": "Panama",
    "357": "Panama",
    "358": "Puerto Rico",
    "359": "El Salvador",
    "361": "Saint Pierre and Miquelon",
    "362": "Trinidad and Tobago",
    "364": "Turks and Caicos",
    "366": "USA",
    "367": "USA",
    "368": "USA",
    "369": "USA",
    "370": "Panama",
    "371": "Panama",
    "372": "Panama",
    "373": "Panama",
    "374": "Panama",
    "375": "Saint Vincent and the Grenadines",
    "376": "Saint Vincent and the Grenadines",
    "377": "Saint Vincent and the Grenadines",
    "378": "British Virgin Islands",
    "379": "US Virgin Islands",
    # Asia
    "401": "Afghanistan",
    "403": "Saudi Arabia",
    "405": "Bangladesh",
    "408": "Bahrain",
    "410": "Bhutan",
    "412": "China",
    "413": "China",
    "414": "China",
    "416": "Taiwan",
    "417": "Sri Lanka",
    "419": "India",
    "422": "Iran",
    "423": "Azerbaijan",
    "425": "Iraq",
    "428": "Israel",
    "431": "Japan",
    "432": "Japan",
    "434": "Turkmenistan",
    "436": "Kazakhstan",
    "437": "Uzbekistan",
    "438": "Jordan",
    "440": "South Korea",
    "441": "South Korea",
    "443": "Palestine",
    "445": "North Korea",
    "447": "Kuwait",
    "450": "Lebanon",
    "451": "Kyrgyzstan",
    "453": "Macao",
    "455": "Maldives",
    "457": "Mongolia",
    "459": "Nepal",
    "461": "Oman",
    "463": "Pakistan",
    "466": "Qatar",
    "468": "Syria",
    "470": "UAE",
    "471": "UAE",
    "472": "Tajikistan",
    "473": "Yemen",
    "475": "Yemen",
    "477": "Hong Kong",
    "478": "Bosnia and Herzegovina",
    # Oceania
    "501": "Adelie Land",
    "503": "Australia",
    "506": "Myanmar",
    "508": "Brunei",
    "510": "Micronesia",
    "511": "Palau",
    "512": "New Zealand",
    "514": "Cambodia",
    "515": "Cambodia",
    "516": "Christmas Island",
    "518": "Cook Islands",
    "520": "Fiji",
    "523": "Cocos Islands",
    "525": "Indonesia",
    "529": "Kiribati",
    "531": "Laos",
    "533": "Malaysia",
    "536": "Northern Mariana Islands",
    "538": "Marshall Islands",
    "540": "New Caledonia",
    "542": "Niue",
    "544": "Nauru",
    "546": "French Polynesia",
    "548": "Philippines",
    "550": "Timor-Leste",
    "553": "Papua New Guinea",
    "555": "Pitcairn Island",
    "557": "Solomon Islands",
    "559": "American Samoa",
    "561": "Samoa",
    "563": "Singapore",
    "564": "Singapore",
    "565": "Singapore",
    "566": "Singapore",
    "567": "Thailand",
    "570": "Tonga",
    "572": "Tuvalu",
    "574": "Vietnam",
    "576": "Vanuatu",
    "577": "Vanuatu",
    "578": "Wallis and Futuna",
    # Africa
    "601": "South Africa",
    "603": "Angola",
    "605": "Algeria",
    "607": "St. Paul and Amsterdam Islands",
    "608": "Ascension Island",
    "609": "Burundi",
    "610": "Benin",
    "611": "Botswana",
    "612": "Central African Republic",
    "613": "Cameroon",
    "615": "Congo",
    "616": "Comoros",
    "617": "Cabo Verde",
    "618": "Crozet Archipelago",
    "619": "Ivory Coast",
    "620": "Comoros",
    "621": "Djibouti",
    "622": "Egypt",
    "624": "Ethiopia",
    "625": "Eritrea",
    "626": "Gabon",
    "627": "Ghana",
    "629": "Gambia",
    "630": "Guinea-Bissau",
    "631": "Equatorial Guinea",
    "632": "Guinea",
    "633": "Burkina Faso",
    "634": "Kenya",
    "635": "Kerguelen Islands",
    "636": "Liberia",
    "637": "Liberia",
    "638": "South Sudan",
    "642": "Libya",
    "644": "Lesotho",
    "645": "Mauritius",
    "647": "Madagascar",
    "649": "Mali",
    "650": "Mozambique",
    "654": "Mauritania",
    "655": "Malawi",
    "656": "Niger",
    "657": "Nigeria",
    "659": "Namibia",
    "660": "Reunion",
    "661": "Rwanda",
    "662": "Sudan",
    "663": "Senegal",
    "664": "Seychelles",
    "665": "Saint Helena",
    "666": "Somalia",
    "667": "Sierra Leone",
    "668": "Sao Tome and Principe",
    "669": "Swaziland",
    "670": "Chad",
    "671": "Togo",
    "672": "Tunisia",
    "674": "Tanzania",
    "675": "Uganda",
    "676": "Democratic Republic of Congo",
    "677": "Tanzania",
    "678": "Zambia",
    "679": "Zimbabwe",
    # South America
    "701": "Argentina",
    "710": "Brazil",
    "720": "Bolivia",
    "725": "Chile",
    "730": "Colombia",
    "735": "Ecuador",
    "740": "Falkland Islands",
    "745": "Guiana",
    "750": "Guyana",
    "755": "Paraguay",
    "760": "Peru",
    "765": "Suriname",
    "770": "Uruguay",
    "775": "Venezuela",
}

# =============================================================================
# VHF Channel Frequencies (MHz) for DSC follow-up
# =============================================================================

VHF_CHANNELS = {
    6: 156.300,
    8: 156.400,
    9: 156.450,
    10: 156.500,
    12: 156.600,
    13: 156.650,
    14: 156.700,
    16: 156.800,
    67: 156.375,
    68: 156.425,
    70: 156.525,
    71: 156.575,
    72: 156.625,
    73: 156.675,
    74: 156.725,
    77: 156.875,
}

# =============================================================================
# DSC Modulation Parameters
# =============================================================================

DSC_BAUD_RATE = 1200

# FSK tone frequencies (Hz) on 1700 Hz subcarrier
DSC_MARK_FREQ = 2100  # B (mark) - binary 0
DSC_SPACE_FREQ = 1300  # Y (space) - binary 1

# Audio sample rate for decoding
DSC_AUDIO_SAMPLE_RATE = 48000

# Frame structure
DSC_DOT_PATTERN_LENGTH = 200
DSC_PHASING_LENGTH = 7
DSC_MESSAGE_MAX_SYMBOLS = 180
