"""
DSC (Digital Selective Calling) constants per ITU-R M.493.

This module contains all DSC-specific constants including format codes,
distress nature codes, telecommand definitions, and MID (Maritime
Identification Digits) country mappings.
"""

from __future__ import annotations

# =============================================================================
# DSC Format Codes (Category)
# Per ITU-R M.493-15 Table 1
# =============================================================================

FORMAT_CODES = {
    102: "GEOGRAPHIC_AREA",  # Geographical area call
    112: "DISTRESS",  # Distress alert
    114: "GROUP",  # Group call (ships with common interest)
    116: "ALL_SHIPS",  # All ships call
    120: "INDIVIDUAL",  # Individual call
    123: "INDIVIDUAL_SEMI_AUTO",  # Individual, semi-automatic/automatic service
}

# Valid ITU-R M.493 format specifiers
VALID_FORMAT_SPECIFIERS = {102, 112, 114, 116, 120, 123}

# Valid EOS (End of Sequence) symbols per ITU-R M.493
VALID_EOS = {117, 122, 127}

# Category priority (lower = higher priority)
CATEGORY_PRIORITY = {
    "DISTRESS": 0,
    "ALL_SHIPS_URGENCY_SAFETY": 2,
    "ALL_SHIPS": 5,
    "GROUP": 5,
    "INDIVIDUAL": 5,
    "INDIVIDUAL_ACK": 5,
}


# =============================================================================
# Nature of Distress Codes
# Per ITU-R M.493-15 Table 3
# =============================================================================

DISTRESS_NATURE_CODES = {
    100: "FIRE_EXPLOSION",  # Fire, explosion
    101: "FLOODING",  # Flooding
    102: "COLLISION",  # Collision
    103: "GROUNDING",  # Grounding
    104: "LISTING_CAPSIZE_DANGER",  # Listing, in danger of capsizing
    105: "SINKING",  # Sinking
    106: "DISABLED_ADRIFT",  # Disabled and adrift
    107: "UNDESIGNATED",  # Undesignated distress
    108: "ABANDONING_SHIP",  # Abandoning ship
    109: "PIRACY_ARMED_ROBBERY",  # Piracy/armed robbery attack
    110: "MAN_OVERBOARD",  # Man overboard
    112: "EPIRB",  # EPIRB emission
}


# =============================================================================
# Telecommand Codes (First and Second)
# Per ITU-R M.493-15 Tables 4-5
# =============================================================================

# Only values confirmed directly against spec text are listed. Anything
# else resolves to UNKNOWN (<code>) rather than a guess, matching the
# decoder's policy -- a wrong label is worse than an honest unknown.
TELECOMMAND_CODES = {
    100: "F3E/G3E ALL MODES TP",  # All modes telephony
    101: "F3E/G3E DUPLEX TP",  # Duplex telephony
    107: "DISTRESS_ACK",  # Distress acknowledgement
    108: "DISTRESS_RELAY",  # Distress relay
    118: "TEST",  # Test
    121: "POSITION_OR_LOCATION_UPDATING",  # Position/location updating
    126: "NO_INFORMATION",  # No information
}

# Full 0-127 telecommand lookup (maps unknown codes to "UNKNOWN")
TELECOMMAND_CODES_FULL = {i: TELECOMMAND_CODES.get(i, "UNKNOWN") for i in range(128)}

# Format codes that carry telecommand fields. Distress (112) carries a
# nature-of-distress symbol instead, so it is excluded.
TELECOMMAND_FORMATS = {102, 114, 116, 120, 123}

# Minimum symbols (after phasing strip) before an EOS can be accepted
MIN_SYMBOLS_FOR_FORMAT = 12


# =============================================================================
# DSC Symbol Definitions
# Per ITU-R M.493-15
# =============================================================================

# Special symbols
DSC_SYMBOLS = {
    120: "DX",  # Dot pattern (synchronization)
    121: "RX",  # Phasing sequence RX
    122: "SX",  # Phasing sequence SX
    123: "S0",  # Phasing sequence S0
    124: "S1",  # Phasing sequence S1
    125: "S2",  # Phasing sequence S2
    126: "S3",  # Phasing sequence S3
    127: "EOS",  # End of sequence
}


# =============================================================================
# MID (Maritime Identification Digits) Country Mapping
# First 3 digits of MMSI identify the country
# Per ITU MID table (partial list of common codes)
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
    6: 156.300,  # Intership safety
    8: 156.400,  # Commercial working
    9: 156.450,  # Calling
    10: 156.500,  # Commercial working
    12: 156.600,  # Port operations
    13: 156.650,  # Bridge-to-bridge navigation safety
    14: 156.700,  # Port operations
    16: 156.800,  # Distress, safety and calling (VHF voice)
    67: 156.375,  # UK small craft safety
    68: 156.425,  # Marina/yacht club
    70: 156.525,  # DSC distress, safety and calling
    71: 156.575,  # Port operations
    72: 156.625,  # Intership
    73: 156.675,  # Port operations
    74: 156.725,  # Port operations
    77: 156.875,  # Intership
}


# =============================================================================
# DSC Modulation Parameters
# =============================================================================

DSC_BAUD_RATE = 1200  # 1200 bps per ITU-R M.493

# FSK tone frequencies (Hz) on 1700 Hz subcarrier
DSC_MARK_FREQ = 2100  # B (mark) - binary 1
DSC_SPACE_FREQ = 1300  # Y (space) - binary 0

# Audio sample rate for decoding
DSC_AUDIO_SAMPLE_RATE = 48000

# Frame structure
DSC_DOT_PATTERN_LENGTH = 200  # 200 bits of alternating pattern
DSC_PHASING_LENGTH = 7  # 7 symbols phasing sequence
DSC_MESSAGE_MAX_SYMBOLS = 180  # Maximum message length in symbols
