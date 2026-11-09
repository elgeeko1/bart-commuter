// SPDX-FileCopyrightText: 2026 Jeff C. Jensen
// SPDX-License-Identifier: BSD-3-Clause
// Version: 0.0.1

/*
 * BART departure boards for Scriptable widgets, Siri, and Shortcuts.
 *
 * A module exporting run(), which renders the boards for however Scriptable launched the script, and
 * api, the version of the run() interface; evaluating it runs nothing.
 *
 * Choosing stations
 *
 *   Set the widget Parameter, the Shortcuts Run Script input, or a ?stations= URL query to one or
 *   two station codes. Case is ignored, and any spaces or punctuation separate codes, so
 *   "woak embr", "WOAK, EMBR" and "woak-embr" are equivalent. DEFAULT_STATIONS applies when empty.
 *
 *   Two stations are a commute: the first board shows trips from the first station to the second,
 *   the second board the return trip. One station shows every train leaving it.
 *   Every line that serves the trip is included, and a trip with one transfer when it arrives sooner
 *   than any direct train leaving at the same time or later.
 *
 * Stations
 *
 *   12TH  12th St Oakland         MCAR  MacArthur
 *   16TH  16th St Mission         MLBR  Millbrae
 *   19TH  19th St Oakland         MLPT  Milpitas
 *   24TH  24th St Mission         MONT  Montgomery St
 *   ANTC  Antioch                 NBRK  North Berkeley
 *   ASHB  Ashby                   NCON  North Concord
 *   BALB  Balboa Park             OAKL  Oakland Airport
 *   BAYF  Bay Fair                ORIN  Orinda
 *   BERY  Berryessa               PCTR  Pittsburg Center
 *   CAST  Castro Valley           PHIL  Pleasant Hill
 *   CIVC  Civic Center            PITT  Pittsburg/Bay Point
 *   COLM  Colma                   PLZA  El Cerrito Plaza
 *   COLS  Coliseum                POWL  Powell St
 *   CONC  Concord                 RICH  Richmond
 *   DALY  Daly City               ROCK  Rockridge
 *   DBRK  Downtown Berkeley       SANL  San Leandro
 *   DELN  El Cerrito del Norte    SBRN  San Bruno
 *   DUBL  Dublin/Pleasanton       SFIA  SFO
 *   EMBR  Embarcadero             SHAY  South Hayward
 *   FRMT  Fremont                 SSAN  South San Francisco
 *   FTVL  Fruitvale               UCTY  Union City
 *   GLEN  Glen Park               WARM  Warm Springs
 *   HAYW  Hayward                 WCRK  Walnut Creek
 *   LAFY  Lafayette               WDUB  West Dublin
 *   LAKE  Lake Merritt            WOAK  West Oakland
 */
const DEFAULT_STATIONS = "woak embr";

// version of the run() interface, incremented when what run() expects or does changes
const ENGINE_API = 1;
// shared public key published by BART at https://www.bart.gov/schedules/developers/api
const API_KEY = "MW9S-E7SL-26DU-VV8V";

const ETD_URL = "https://api.bart.gov/api/etd.aspx";
const SCHEDULE_URL = "https://api.bart.gov/api/sched.aspx";
const GTFS_RT_URL = "https://api.bart.gov/gtfsrt/tripupdate.aspx";
// schedule times are local to BART regardless of the device time zone
const BART_TIME_ZONE = "America/Los_Angeles";
const TRAINS_PER_BOARD = 3;
const TRAINS_PER_LIST = 7;
// widget families that list later trains one per row
const LIST_FAMILIES = ["large", "extraLarge"];
// trips requested from the trip planner, whose maximum is 4, some of which may need a transfer
const TRIPS_PLANNED = 4;
// trips requested from the trip planner that were timetabled to leave before now, whose first train may
// still be at the platform running late; the b parameter of the planner
const TRIPS_BEFORE = 1;
// requests still pending this long after run() starts fail, so every run renders in bounded time: iOS
// shows a widget only once its run ends, and ends an overlong run with an error
const RUN_DEADLINE_MS = 15 * 1000;
const MAX_FEED_AGE_S = 5 * 60;
// earliest widget reload; iOS throttles reloads and picks the actual time
const REFRESH_MS = 60 * 1000;
const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * 60 * MINUTE_MS;
const DAY_S = DAY_MS / 1000;

const ETD_DIRECTIONS = { North: "n", South: "s" };
// ETD color of a train running outside the timetable, which has a dot and letter of its own
const UNSCHEDULED = { line: "WHITE", letter: "U", color: "#ffffff" };
// ETD lists at most this many trains per destination, so a full list may omit later trains
const ETD_MAX_PER_DESTINATION = 3;
// GTFS-RT lists every trip starting within this many minutes, so it holds every departure until then
const GTFS_RT_HORIZON_MIN = 43;
// an ETD estimate and a GTFS-RT departure of one line at most this far apart are the same train
const SAME_TRAIN_MIN = 5;
// lines absent from both ETD and GTFS-RT
const UNTRACKED_LINES = ["GREY"];
// a live train runs a planned leg when it leaves no more than this many minutes early or late
const LEG_MATCH_MIN = { early: 1, late: 10 };
// whole minutes a transfer needs between the projected arrival of the first train and the projected
// departure of the connecting train
const TRANSFER_SLACK_MIN = 0;
// the nightly meet of the last trains at MacArthur, whose connections always hold, by the connecting
// trains' timetable departures
const GRAND_MEET = { station: "MCAR", first: "12:51 AM", last: "12:52 AM" };
// stations where BART times connections between trains running in the given direction
const TIMED_TRANSFERS = [
  { station: "19TH", dir: "n" },
  { station: "MCAR", dir: "s" },
  { station: "BAYF", dir: "n" },
  { station: "BAYF", dir: "s" },
];
// times before this many minutes past midnight belong to the end of the previous service day
const SERVICE_DAY_START_MIN = 3 * 60;
// Pacific standard and daylight time, in hours behind UTC
const BART_UTC_OFFSETS_H = [8, 7];
// whether a train serves a board; UNSURE when it may skip a station
const YES = "yes";
const NO = "no";
const UNSURE = "unsure";
// whether a live train runs a planned leg
const RUNNING = "running";
const NOT_RUNNING = "not running";
const UNKNOWN = "unknown";

// BEGIN NETWORK: generated by tools/generate_network.py from BART's static GTFS; do not edit
const STATIONS = {
  "12TH": { name: "12th St Oakland", platforms: ["K10-1", "K10-2", "K10-3"] },
  "16TH": { name: "16th St Mission", platforms: ["M50-1", "M50-2"] },
  "19TH": { name: "19th St Oakland", platforms: ["K20-1", "K20-2", "K20-3"] },
  "24TH": { name: "24th St Mission", platforms: ["M60-1", "M60-2", "M60-B"] },
  ANTC: { name: "Antioch", platforms: ["E30-1", "E30-2"] },
  ASHB: { name: "Ashby", platforms: ["R10-1", "R10-2"] },
  BALB: { name: "Balboa Park", platforms: ["M80-1", "M80-2", "M80-B"] },
  BAYF: { name: "Bay Fair", platforms: ["A50-1", "A50-2"] },
  BERY: { name: "Berryessa", platforms: ["S50-1", "S50-2"] },
  CAST: { name: "Castro Valley", platforms: ["L10-1", "L10-2"] },
  CIVC: { name: "Civic Center", platforms: ["M40-1", "M40-2"] },
  COLM: { name: "Colma", platforms: ["W10-1", "W10-2"] },
  COLS: { name: "Coliseum", platforms: ["A30-1", "A30-2", "H10"] },
  CONC: { name: "Concord", platforms: ["C60-1", "C60-2"] },
  DALY: { name: "Daly City", platforms: ["M90-1", "M90-2", "M90-3", "M90-B"] },
  DBRK: { name: "Downtown Berkeley", platforms: ["R20-1", "R20-2"] },
  DELN: { name: "El Cerrito del Norte", platforms: ["R50-1", "R50-2"] },
  DUBL: { name: "Dublin/Pleasanton", platforms: ["L30-1", "L30-2"] },
  EMBR: { name: "Embarcadero", platforms: ["M16-1", "M16-2"] },
  FRMT: { name: "Fremont", platforms: ["A90-1", "A90-2"] },
  FTVL: { name: "Fruitvale", platforms: ["A20-1", "A20-2"] },
  GLEN: { name: "Glen Park", platforms: ["M70-1", "M70-2", "M70-B"] },
  HAYW: { name: "Hayward", platforms: ["A60-1", "A60-2"] },
  LAFY: { name: "Lafayette", platforms: ["C30-1", "C30-2"] },
  LAKE: { name: "Lake Merritt", platforms: ["A10-1", "A10-2"] },
  MCAR: { name: "MacArthur", platforms: ["K30-1", "K30-2", "K30-3", "K30-4"] },
  MLBR: { name: "Millbrae", platforms: ["W40-3"] },
  MLPT: { name: "Milpitas", platforms: ["S40-1", "S40-2"] },
  MONT: { name: "Montgomery St", platforms: ["M20-1", "M20-2"] },
  NBRK: { name: "North Berkeley", platforms: ["R30-1", "R30-2"] },
  NCON: { name: "North Concord", platforms: ["C70-1", "C70-2"] },
  OAKL: { name: "Oakland Airport", platforms: ["H40"] },
  ORIN: { name: "Orinda", platforms: ["C20-1", "C20-2"] },
  PCTR: { name: "Pittsburg Center", platforms: ["E20-1", "E20-2"] },
  PHIL: { name: "Pleasant Hill", platforms: ["C50-1", "C50-2"] },
  PITT: { name: "Pittsburg/Bay Point", platforms: ["C80-1", "C80-2"] },
  PLZA: { name: "El Cerrito Plaza", platforms: ["R40-1", "R40-2"] },
  POWL: { name: "Powell St", platforms: ["M30-1", "M30-2"] },
  RICH: { name: "Richmond", platforms: ["R60-1", "R60-2"] },
  ROCK: { name: "Rockridge", platforms: ["C10-1", "C10-2"] },
  SANL: { name: "San Leandro", platforms: ["A40-1", "A40-2"] },
  SBRN: { name: "San Bruno", platforms: ["W30-1", "W30-2"] },
  SFIA: { name: "SFO", platforms: ["Y10-1", "Y10-2", "Y10-3"] },
  SHAY: { name: "South Hayward", platforms: ["A70-1", "A70-2"] },
  SSAN: { name: "South San Francisco", platforms: ["W20-1", "W20-2"] },
  UCTY: { name: "Union City", platforms: ["A80-1", "A80-2"] },
  WARM: { name: "Warm Springs", platforms: ["S20-1", "S20-2"] },
  WCRK: { name: "Walnut Creek", platforms: ["C40-1", "C40-2"] },
  WDUB: { name: "West Dublin", platforms: ["L20-1", "L20-2"] },
  WOAK: { name: "West Oakland", platforms: ["M10-1", "M10-2"] },
};
const LINE_COLORS = {
  BLUE: "#0099cc",
  GREEN: "#339933",
  GREY: "#b0bec7",
  ORANGE: "#ff9933",
  RED: "#ff0000",
  YELLOW: "#ffff33",
};
// number is the schedule API's route number; stations are in travel order, and some
// trips pass skips without stopping
const ROUTES = [
  {
    number: 1,
    line: "YELLOW",
    dir: "s",
    stations: [
      "ANTC",
      "PCTR",
      "PITT",
      "NCON",
      "CONC",
      "PHIL",
      "WCRK",
      "LAFY",
      "ORIN",
      "ROCK",
      "MCAR",
      "19TH",
      "12TH",
      "WOAK",
      "EMBR",
      "MONT",
      "POWL",
      "CIVC",
      "16TH",
      "24TH",
      "GLEN",
      "BALB",
      "DALY",
      "COLM",
      "SSAN",
      "SBRN",
      "SFIA",
      "MLBR",
    ],
    skips: ["SFIA"],
  },
  {
    number: 2,
    line: "YELLOW",
    dir: "n",
    stations: [
      "MLBR",
      "SFIA",
      "SBRN",
      "SSAN",
      "COLM",
      "DALY",
      "BALB",
      "GLEN",
      "24TH",
      "16TH",
      "CIVC",
      "POWL",
      "MONT",
      "EMBR",
      "WOAK",
      "12TH",
      "19TH",
      "MCAR",
      "ROCK",
      "ORIN",
      "LAFY",
      "WCRK",
      "PHIL",
      "CONC",
      "NCON",
      "PITT",
      "PCTR",
      "ANTC",
    ],
    skips: [],
  },
  {
    number: 3,
    line: "ORANGE",
    dir: "n",
    stations: [
      "BERY",
      "MLPT",
      "WARM",
      "FRMT",
      "UCTY",
      "SHAY",
      "HAYW",
      "BAYF",
      "SANL",
      "COLS",
      "FTVL",
      "LAKE",
      "12TH",
      "19TH",
      "MCAR",
      "ASHB",
      "DBRK",
      "NBRK",
      "PLZA",
      "DELN",
      "RICH",
    ],
    skips: [],
  },
  {
    number: 4,
    line: "ORANGE",
    dir: "s",
    stations: [
      "RICH",
      "DELN",
      "PLZA",
      "NBRK",
      "DBRK",
      "ASHB",
      "MCAR",
      "19TH",
      "12TH",
      "LAKE",
      "FTVL",
      "COLS",
      "SANL",
      "BAYF",
      "HAYW",
      "SHAY",
      "UCTY",
      "FRMT",
      "WARM",
      "MLPT",
      "BERY",
    ],
    skips: [],
  },
  {
    number: 5,
    line: "GREEN",
    dir: "s",
    stations: [
      "BERY",
      "MLPT",
      "WARM",
      "FRMT",
      "UCTY",
      "SHAY",
      "HAYW",
      "BAYF",
      "SANL",
      "COLS",
      "FTVL",
      "LAKE",
      "WOAK",
      "EMBR",
      "MONT",
      "POWL",
      "CIVC",
      "16TH",
      "24TH",
      "GLEN",
      "BALB",
      "DALY",
    ],
    skips: [],
  },
  {
    number: 6,
    line: "GREEN",
    dir: "n",
    stations: [
      "DALY",
      "BALB",
      "GLEN",
      "24TH",
      "16TH",
      "CIVC",
      "POWL",
      "MONT",
      "EMBR",
      "WOAK",
      "LAKE",
      "FTVL",
      "COLS",
      "SANL",
      "BAYF",
      "HAYW",
      "SHAY",
      "UCTY",
      "FRMT",
      "WARM",
      "MLPT",
      "BERY",
    ],
    skips: [],
  },
  {
    number: 7,
    line: "RED",
    dir: "s",
    stations: [
      "RICH",
      "DELN",
      "PLZA",
      "NBRK",
      "DBRK",
      "ASHB",
      "MCAR",
      "19TH",
      "12TH",
      "WOAK",
      "EMBR",
      "MONT",
      "POWL",
      "CIVC",
      "16TH",
      "24TH",
      "GLEN",
      "BALB",
      "DALY",
      "COLM",
      "SSAN",
      "SBRN",
      "SFIA",
      "MLBR",
    ],
    skips: [],
  },
  {
    number: 8,
    line: "RED",
    dir: "n",
    stations: [
      "MLBR",
      "SFIA",
      "SBRN",
      "SSAN",
      "COLM",
      "DALY",
      "BALB",
      "GLEN",
      "24TH",
      "16TH",
      "CIVC",
      "POWL",
      "MONT",
      "EMBR",
      "WOAK",
      "12TH",
      "19TH",
      "MCAR",
      "ASHB",
      "DBRK",
      "NBRK",
      "PLZA",
      "DELN",
      "RICH",
    ],
    skips: [],
  },
  {
    number: 11,
    line: "BLUE",
    dir: "s",
    stations: [
      "DUBL",
      "WDUB",
      "CAST",
      "BAYF",
      "SANL",
      "COLS",
      "FTVL",
      "LAKE",
      "WOAK",
      "EMBR",
      "MONT",
      "POWL",
      "CIVC",
      "16TH",
      "24TH",
      "GLEN",
      "BALB",
      "DALY",
    ],
    skips: [],
  },
  {
    number: 12,
    line: "BLUE",
    dir: "n",
    stations: [
      "DALY",
      "BALB",
      "GLEN",
      "24TH",
      "16TH",
      "CIVC",
      "POWL",
      "MONT",
      "EMBR",
      "WOAK",
      "LAKE",
      "FTVL",
      "COLS",
      "SANL",
      "BAYF",
      "CAST",
      "WDUB",
      "DUBL",
    ],
    skips: [],
  },
  { number: 19, line: "GREY", dir: "n", stations: ["OAKL", "COLS"], skips: [] },
  { number: 20, line: "GREY", dir: "s", stations: ["COLS", "OAKL"], skips: [] },
];
// END NETWORK

// BEGIN TEXT WIDTHS: generated by tools/measure_fonts.js from SF Pro; do not edit
const HEADINGS = {
  compact: { origin: 11, destination: 9, arrow: 13.8, column: 0 },
  large: { origin: 22, destination: 18, arrow: 25, column: 2 },
  comfortable: { origin: 12, destination: 10, arrow: 15.1, column: 4 },
  spacious: { origin: 13, destination: 11, arrow: 16.4, column: 6 },
};
// heading widths in points of each station's name and code, as origin and as destination, per heading kind
const HEADING_WIDTHS = {
  "12TH": {
    name: [88.4, 69.6, 162.3, 126.9, 95.4, 76.3, 102.2, 83],
    code: [28.9, 22.7, 54, 42.1, 31.2, 25, 33.5, 27.2],
  },
  "16TH": {
    name: [85.8, 67.4, 157.2, 122.5, 92.5, 73.9, 99.1, 80.3],
    code: [29.3, 23, 55, 42.8, 31.7, 25.3, 34, 27.5],
  },
  "19TH": {
    name: [88.8, 69.9, 163.3, 127.5, 95.9, 76.6, 102.7, 83.3],
    code: [29.3, 23, 55, 42.8, 31.7, 25.3, 34, 27.5],
  },
  "24TH": {
    name: [87.2, 68.7, 160, 125.1, 94.1, 75.3, 100.8, 81.9],
    code: [30.8, 24.3, 57.8, 45.4, 33.3, 26.7, 35.8, 29.2],
  },
  ANTC: {
    name: [43.4, 33.7, 80.1, 61.6, 46.8, 36.9, 50.2, 40.2],
    code: [32.2, 25.6, 60.8, 47.9, 34.9, 28.2, 37.5, 30.7],
  },
  ASHB: { name: [35.3, 27.4, 65.8, 50.6, 38.2, 30.1, 41, 32.7], code: [32, 25.1, 60.2, 46.9, 34.7, 27.6, 37.2, 30.1] },
  BALB: { name: [66.7, 52.4, 122.3, 95.7, 72, 57.4, 77.2, 62.5], code: [30.2, 23.7, 56.4, 44.1, 32.6, 26.1, 35, 28.4] },
  BAYF: {
    name: [45.1, 35.5, 82.1, 64.3, 48.6, 38.8, 52.1, 42.2],
    code: [30.4, 23.7, 56.9, 44.2, 32.8, 26.1, 35.3, 28.5],
  },
  BERY: {
    name: [56.6, 43.9, 103.9, 80.3, 61.1, 48.1, 65.5, 52.4],
    code: [30.2, 23.8, 56.5, 44.2, 32.7, 26.1, 35.1, 28.5],
  },
  CAST: {
    name: [74.8, 58.5, 137, 106.3, 80.7, 64.1, 86.4, 69.7],
    code: [31.2, 24.7, 58.8, 46, 33.8, 27.1, 36.3, 29.6],
  },
  CIVC: { name: [68.6, 54.1, 125.8, 98.3, 74, 59.2, 79.2, 64.4], code: [28, 22.1, 52.7, 40.9, 30.3, 24.2, 32.5, 26.4] },
  COLM: {
    name: [35.3, 27.7, 65.9, 51.3, 38.2, 30.4, 41, 33.2],
    code: [33.6, 27.1, 63.8, 50.9, 36.4, 29.8, 39.1, 32.5],
  },
  COLS: {
    name: [51.9, 40.6, 96.2, 74.6, 56.1, 44.5, 60.1, 48.5],
    code: [31.1, 24.9, 58.7, 46.6, 33.6, 27.4, 36.1, 29.9],
  },
  CONC: {
    name: [47.6, 37.5, 88.4, 69.3, 51.4, 41.2, 55.2, 44.9],
    code: [33.7, 27.2, 64.1, 51.2, 36.5, 30, 39.2, 32.7],
  },
  DALY: {
    name: [50.4, 39.6, 92, 71.8, 54.3, 43.4, 58.2, 47.1],
    code: [30.9, 24.3, 58.1, 45.4, 33.4, 26.8, 35.9, 29.2],
  },
  DBRK: {
    name: [111.5, 87.1, 205.7, 160.2, 120.4, 95.6, 129.1, 104.1],
    code: [31.6, 25, 59.1, 46.6, 34.2, 27.5, 36.7, 30],
  },
  DELN: {
    name: [107, 84.7, 193.7, 152.8, 115.3, 92.7, 123.4, 100.7],
    code: [30.4, 24.4, 56.8, 45.5, 32.8, 26.8, 35.3, 29.3],
  },
  DUBL: {
    name: [102, 79.6, 187.8, 145.2, 110.1, 87.2, 118, 94.9],
    code: [31.1, 24.9, 58.1, 46.5, 33.6, 27.4, 36.1, 29.9],
  },
  EMBR: {
    name: [74.6, 58.4, 138.1, 107.7, 80.6, 64.2, 86.5, 69.9],
    code: [32.4, 25.7, 60.8, 48.2, 35, 28.3, 37.7, 30.9],
  },
  FRMT: {
    name: [47, 36.6, 86.7, 67.4, 50.7, 40.2, 54.4, 43.8],
    code: [31.7, 25.3, 59.5, 47.3, 34.4, 27.9, 36.9, 30.4],
  },
  FTVL: {
    name: [49.3, 38.1, 89.7, 68.9, 53.2, 41.8, 56.9, 45.3],
    code: [28.6, 22.7, 53.3, 42.2, 30.9, 25, 33.2, 27.2],
  },
  GLEN: { name: [53.9, 42.5, 98.7, 77.5, 58.1, 46.6, 62.3, 50.6], code: [30.5, 24.6, 57.2, 45.9, 33, 27, 35.4, 29.5] },
  HAYW: { name: [49.9, 38.6, 92.7, 71.5, 54, 42.5, 57.9, 46.2], code: [35.9, 28.1, 68, 52.9, 38.8, 30.9, 41.8, 33.7] },
  LAFY: {
    name: [53.5, 41.6, 98.2, 75.7, 57.7, 45.6, 61.8, 49.5],
    code: [29.3, 22.9, 54.8, 42.6, 31.6, 25.2, 34, 27.5],
  },
  LAKE: {
    name: [68.3, 53.3, 124.3, 96.7, 73.7, 58.4, 78.9, 63.5],
    code: [29.5, 23.2, 55.2, 43.1, 31.9, 25.5, 34.3, 27.8],
  },
  MCAR: { name: [59.6, 46.2, 110.2, 84.9, 64.3, 50.7, 69, 55.1], code: [34.1, 27, 64.7, 50.7, 36.9, 29.7, 39.7, 32.4] },
  MLBR: {
    name: [45.2, 35.1, 82.5, 63.6, 48.8, 38.4, 52.2, 41.8],
    code: [32.1, 25.5, 60.2, 47.7, 34.7, 28, 37.3, 30.6],
  },
  MLPT: {
    name: [44.5, 34.4, 81.2, 62.3, 47.9, 37.7, 51.3, 40.9],
    code: [31.5, 25.1, 58.9, 46.9, 34.1, 27.6, 36.6, 30.1],
  },
  MONT: {
    name: [86, 67.5, 158.8, 124.4, 92.9, 74.2, 99.7, 80.7],
    code: [34.6, 27.9, 65.5, 52.5, 37.5, 30.7, 40.3, 33.5],
  },
  NBRK: {
    name: [84, 65.7, 153.5, 119.9, 90.6, 72.1, 97.1, 78.4],
    code: [31.8, 25.1, 59.5, 46.9, 34.4, 27.6, 37, 30.1],
  },
  NCON: {
    name: [82.4, 65.1, 151.7, 119.4, 88.9, 71.4, 95.3, 77.7],
    code: [34, 27.5, 64.4, 51.7, 36.8, 30.2, 39.6, 33],
  },
  OAKL: {
    name: [88.7, 69.1, 162.3, 125.9, 95.7, 75.8, 102.5, 82.4],
    code: [31.4, 24.8, 59, 46.2, 33.9, 27.2, 36.4, 29.7],
  },
  ORIN: {
    name: [37.6, 29.4, 69.3, 53.8, 40.6, 32.2, 43.5, 35.1],
    code: [28.5, 22.6, 53.2, 42, 30.9, 24.9, 33.1, 27.1],
  },
  PCTR: {
    name: [93.3, 72.8, 170.5, 132.4, 100.7, 79.8, 107.9, 86.7],
    code: [30.8, 24.5, 57.6, 45.6, 33.3, 26.9, 35.7, 29.3],
  },
  PHIL: { name: [69.9, 54.5, 127, 98.3, 75.3, 59.7, 80.6, 64.8], code: [26.4, 20.6, 48.7, 38, 28.5, 22.6, 30.6, 24.6] },
  PITT: {
    name: [109.1, 84.9, 199.2, 154.2, 117.7, 93.1, 126.1, 101.1],
    code: [25.7, 20.3, 47.3, 37.2, 27.8, 22.2, 29.8, 24.2],
  },
  PLZA: {
    name: [85.5, 67.4, 155, 121.7, 92.2, 73.8, 98.6, 80.2],
    code: [29.7, 23.6, 55.7, 43.8, 32.2, 25.9, 34.5, 28.2],
  },
  POWL: {
    name: [52.1, 40.8, 95.1, 74.1, 56.2, 44.7, 60.2, 48.6],
    code: [34, 27.2, 64.3, 51.1, 36.8, 29.9, 39.6, 32.7],
  },
  RICH: {
    name: [55.9, 43.7, 103.9, 80.9, 60.4, 48.1, 64.8, 52.3],
    code: [28.2, 22.1, 52.6, 41, 30.5, 24.3, 32.7, 26.5],
  },
  ROCK: { name: [57, 44.5, 104.9, 81.5, 61.5, 48.8, 66, 53.1], code: [32.6, 25.9, 61.5, 48.5, 35.2, 28.5, 37.9, 31.1] },
  SANL: {
    name: [69.7, 55.1, 128.6, 101.2, 75.3, 60.5, 80.7, 65.8],
    code: [30.8, 24.3, 57.8, 45.3, 33.3, 26.7, 35.8, 29.1],
  },
  SBRN: { name: [57.3, 45.2, 105.6, 83, 61.9, 49.6, 66.3, 54], code: [31.4, 24.9, 58.9, 46.6, 34, 27.4, 36.5, 29.9] },
  SFIA: { name: [22.9, 18.4, 43.1, 34.3, 24.8, 20.2, 26.6, 22], code: [25.9, 20.1, 48, 36.9, 27.9, 22, 29.9, 24] },
  SHAY: {
    name: [85.8, 67, 158.5, 123.4, 92.7, 73.6, 99.4, 80.1],
    code: [32.2, 25.1, 60.7, 46.9, 34.8, 27.6, 37.4, 30.1],
  },
  SSAN: {
    name: [114.3, 90.2, 210.1, 164.8, 123.3, 98.9, 132.1, 107.6],
    code: [31.7, 24.9, 59.6, 46.6, 34.3, 27.4, 36.8, 29.9],
  },
  UCTY: {
    name: [58.1, 45.8, 106.5, 83.3, 62.7, 50.1, 67.1, 54.5],
    code: [31.9, 25.4, 60, 47.5, 34.5, 27.9, 37.1, 30.5],
  },
  WARM: {
    name: [79, 61.8, 145.7, 113.8, 85.3, 67.9, 91.5, 73.9],
    code: [37.1, 29.2, 70.4, 55.2, 40.2, 32.2, 43.2, 35.2],
  },
  WCRK: {
    name: [75.6, 59.3, 139.4, 108.8, 81.6, 65.1, 87.5, 70.9],
    code: [35, 27.7, 66.3, 52.1, 37.9, 30.5, 40.7, 33.3],
  },
  WDUB: {
    name: [67.7, 53.3, 124.5, 97.6, 73, 58.5, 78.3, 63.6],
    code: [35.6, 28.5, 67.3, 53.7, 38.6, 31.4, 41.5, 34.3],
  },
  WOAK: {
    name: [77.7, 61.2, 143.7, 112.6, 83.9, 67.2, 90, 73.2],
    code: [35.9, 28.4, 68.2, 53.4, 38.9, 31.2, 41.8, 34.1],
  },
};
const DEPARTURE_SIZE = 26;
const TRANSFER_SIZES = { small: 10, columns: 12, large: 14, list: 14 };
const UPDATED_SIZE = 9;
// widest age under an hour in points at the updated line's size
const AGE_WIDTH = 55.4;
// END TEXT WIDTHS

const STATION_OF_PLATFORM = Object.fromEntries(
  Object.entries(STATIONS).flatMap(([code, s]) => s.platforms.map((id) => [id, code])),
);

// gtfs-realtime.proto field numbers
const FEED = { header: 1, entity: 2 };
const HEADER = { timestamp: 3 };
const ENTITY = { tripUpdate: 3 };
const TRIP_UPDATE = { trip: 1, stopTimeUpdate: 2 };
const TRIP_DESCRIPTOR = { scheduleRelationship: 4 };
const STOP_TIME_UPDATE = { arrival: 2, departure: 3, stopId: 4, scheduleRelationship: 5 };
// schedule_relationship values of trips (SCHEDULED, ADDED, UNSCHEDULED, CANCELED, DELETED) and stops
// (SCHEDULED, SKIPPED, NO_DATA, UNSCHEDULED)
const TRIP_RUNS = [0, 1, 2];
const TRIP_DROPPED = [3, 7];
const STOP_SERVED = [0, 2, 3];
const STOP_SKIPPED = 1;
const STOP_TIME_EVENT = { time: 2 };

const BART_CLOCK = new Intl.DateTimeFormat("en-US", {
  timeZone: BART_TIME_ZONE,
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "numeric",
  minute: "numeric",
  hourCycle: "h23",
});

/**
 * @typedef {{ number: number, line: string, dir: string, stations: string[], skips: string[] }} Route
 * @typedef {{ id: string, from: string, to: string | null, title: string, subtitle: string | null,
 *   spoken: string, shown: number }} Board
 *   to is null for a board of every train leaving one station; shown is the number of trains displayed
 * @typedef {{ minutes: number, departs: Date, line: string | null, scheduled: boolean }} Train
 *   one departure of a train; minutes is whole minutes to departure, 0 meaning leaving; line is null when
 *   the source cannot tell; scheduled marks a timetable time rather than a live one
 * @typedef {Train & { arrives: Date | null, transfer: { station: string, line: string } | null }} Trip
 *   a way to ride a board, leaving on this train; arrives is the estimated arrival at the commute
 *   destination, null when unknown; transfer is the station where the trip changes trains and the
 *   connecting line
 * @typedef {{ line: string, dir: string, from: string, to: string, departs: Date, arrives: Date }} Leg
 *   the part of a planned trip on one train
 * @typedef {{ status: string, train: Train | null }} LegRun
 *   whether a live train runs a planned leg, as RUNNING, NOT_RUNNING or UNKNOWN; train is set when RUNNING
 * @typedef {{ board: Board, trips: Trip[] | null, last: boolean }} LoadedBoard
 *   trips is null when no source has data; last marks the final listed trip as leaving on the last train
 *   of the service
 */

/**
 * Departures from one or more sources, and how far they can be trusted.
 * @template T
 * @typedef {{ departures: T[], complete: boolean, until: number, checked: number }} DepartureList
 *   complete when departures holds every remaining departure of the service day; until is the epoch ms
 *   after which a departure may be missing; checked is the epoch ms up to which a timetable train absent
 *   from departures does not run, 0 when nothing rules one out
 */

/**
 * Parses one or two station codes.
 * @returns {string[]} empty when raw contains no codes
 */
function parseStations(raw) {
  const codes = raw
    .toUpperCase()
    .split(/[^A-Z0-9]+/)
    .filter(Boolean);
  for (const code of codes) {
    if (!STATIONS[code]) throw new Error(`unknown station: ${code}`);
  }
  if (codes.length > 2) throw new Error("max two stations");
  if (codes.length === 2 && codes[0] === codes[1]) throw new Error(`same station twice: ${codes[0]}`);
  return codes;
}

function commuteBoard(from, to, shown) {
  const name = STATIONS[from].name;
  return {
    shown,
    id: `${from}-${to}`,
    from,
    to,
    title: name,
    subtitle: `to ${STATIONS[to].name}`,
    spoken: `${name} to ${STATIONS[to].name}`,
  };
}

function stationBoard(from, shown) {
  const name = STATIONS[from].name;
  return { shown, id: from, from, to: null, title: name, subtitle: null, spoken: name };
}

/** @returns {Board[]} the outbound and return commute, or every train leaving one station */
function boardsFor(codes, shown) {
  if (codes.length === 1) return [stationBoard(codes[0], shown)];
  return [commuteBoard(codes[0], codes[1], shown), commuteBoard(codes[1], codes[0], shown)];
}

// whether a route stops at a and later at b
function stopsInOrder(route, a, b) {
  const i = route.stations.indexOf(a);
  return i >= 0 && route.stations.indexOf(b) > i;
}

// YES when a train of this line and direction bound for dest stops at from and then at to, UNSURE when it may skip the to station
function servesDirect(line, dir, from, to, dest) {
  const route = ROUTES.find((r) => r.line === line && r.dir === dir);
  if (!route || !stopsInOrder(route, from, to) || (dest !== to && !stopsInOrder(route, to, dest))) return NO;
  return dest !== to && route.skips.includes(to) ? UNSURE : YES;
}

function hasDirectLine(board) {
  return ROUTES.some((r) => stopsInOrder(r, board.from, board.to));
}

// whether a line serving the board has no live predictions
function isUntracked(board) {
  return ROUTES.some((r) => {
    if (!UNTRACKED_LINES.includes(r.line)) return false;
    if (board.to) return stopsInOrder(r, board.from, board.to);
    const i = r.stations.indexOf(board.from);
    return i >= 0 && i < r.stations.length - 1;
  });
}

/**
 * Returns the confirmed trains, or throws when an unconfirmed train could be among those shown.
 * @param {Train[]} confirmed  trains known to serve the board
 * @param {Train[]} unsure  trains that may or may not serve the board
 */
function confirmTrains(board, confirmed, unsure) {
  confirmed.sort((a, b) => a.departs - b.departs);
  const last = confirmed[board.shown - 1];
  const cutoff = last ? last.departs.getTime() : Infinity;
  if (unsure.some((t) => t.departs.getTime() <= cutoff)) throw new Error(`cannot confirm trains to ${board.to}`);
  return confirmed;
}

// epoch ms after which a request fails, which run() sets
let deadline = Infinity;

/**
 * Settles as the promise of a request does, or fails once the deadline passes. The timeoutInterval of a
 * request bounds only how long it waits for data, and a source that fails falls back to others requested
 * alongside it, so a request has no limit of its own.
 */
function beforeDeadline(promise) {
  let timer = null;
  const expired = new Promise((_, reject) => {
    timer = Timer.schedule(Math.max(0, deadline - Date.now()), false, () => reject(new Error("timed out")));
  });
  return Promise.race([promise, expired]).finally(() => timer.invalidate());
}

/**
 * Requests a legacy API endpoint, which reports errors such as a rejected key in a JSON body.
 * @param {Record<string, string | number>} params  query parameters other than key and json
 */
async function loadLegacyApi(url, params) {
  const query = Object.entries({ ...params, key: API_KEY, json: "y" })
    .map(([name, value]) => `${name}=${encodeURIComponent(value)}`)
    .join("&");
  const req = new Request(`${url}?${query}`);
  const text = (await beforeDeadline(req.load())).toRawString();
  const status = req.response.statusCode;
  let json;
  try {
    json = JSON.parse(text);
  } catch (e) {
    // a failing server may answer with a page that is not JSON, which says no more than its status
    throw new Error(status === 200 ? e.message : `HTTP ${status}`, { cause: e });
  }
  const error = json.root.message?.error;
  if (error) throw new Error(error.text);
  if (status !== 200) throw new Error(`HTTP ${status}`);
  return json.root;
}

/**
 * Loads every predicted departure at a station.
 * @returns {Promise<{ minutes: number, departs: Date, line: string, dest: string, dir: string, listEnd: Date,
 *   full: boolean, cancelled: boolean }[]>}
 *   listEnd is the last departure in the destination list of the train, and full marks a list that may omit
 *   later trains
 */
async function loadEtd(code) {
  const root = await loadLegacyApi(ETD_URL, { cmd: "etd", orig: code });
  const fetchedAt = Date.now();
  const at = (minutes) => new Date(fetchedAt + minutes * MINUTE_MS);
  const trains = [];
  // etd is omitted when no departures are predicted
  for (const etd of root.station[0].etd ?? []) {
    const minutes = etd.estimate.map((est) => (est.minutes === "Leaving" ? 0 : Number.parseInt(est.minutes, 10)));
    const bad = etd.estimate.find((_, i) => !Number.isInteger(minutes[i]));
    if (bad) throw new Error(`unexpected minutes value: ${bad.minutes}`);
    const listEnd = at(Math.max(...minutes));
    const full = minutes.length >= ETD_MAX_PER_DESTINATION;
    etd.estimate.forEach((est, i) => {
      const dir = ETD_DIRECTIONS[est.direction];
      if (!dir) throw new Error(`unexpected direction: ${est.direction}`);
      if (!(est.color in LINE_COLORS) && est.color !== UNSCHEDULED.line)
        throw new Error(`unexpected line color: ${est.color}`);
      trains.push({
        minutes: minutes[i],
        departs: at(minutes[i]),
        line: est.color,
        dest: etd.abbreviation,
        dir,
        listEnd,
        full,
        cancelled: est.cancelflag === "1",
      });
    });
  }
  return trains;
}

function liveTrain(minutes, departs, line) {
  return { minutes, departs, line, scheduled: false };
}

// whole minutes from the start of the current minute to a timetable departure
function minutesUntil(departs) {
  const now = Date.now();
  // the UTC offset of BART time is whole hours, so minute boundaries match UTC
  return Math.round((departs - (now - (now % MINUTE_MS))) / MINUTE_MS);
}

function scheduledTrain(departs, line) {
  return { minutes: minutesUntil(departs), departs, line, scheduled: true };
}

/**
 * @param {Train} train
 * @returns {Trip} the trip leaving on a train, without a known arrival or transfer
 */
function asTrip(train) {
  return { ...train, arrives: null, transfer: null };
}

/**
 * Returns the trains of the board among ETD estimates.
 * @param {number | null} covered  epoch ms up to which another source supplies any train ETD omits
 * @param {number} checked  checked of the returned list
 * @returns {(DepartureList<Train> & { cancelled: Train[] }) | null} cancelled holds the trains BART
 *   cancelled; null when no running estimate serves the board, as before service starts, and no other
 *   source covers it
 */
function trainsFromEtd(estimates, board, covered, checked) {
  const serving = [];
  for (const { minutes, departs, line, dest, dir, listEnd, full, cancelled } of estimates) {
    const serves = board.to ? servesDirect(line, dir, board.from, board.to, dest) : YES;
    if (serves !== NO) serving.push({ serves, listEnd, full, cancelled, train: liveTrain(minutes, departs, line) });
  }
  const running = serving.filter((e) => !e.cancelled);
  const cancelled = serving.filter((e) => e.cancelled).map((e) => e.train);
  if (running.length === 0) {
    return covered === null ? null : { departures: [], complete: false, until: covered, checked, cancelled };
  }
  // a full destination list may omit later trains, and a shorter one omits only trains far ahead
  const coverage = (e) => {
    if (e.full) return e.listEnd.getTime();
    return covered === null ? Infinity : Math.max(e.listEnd.getTime(), covered);
  };
  const until = Math.min(...running.map(coverage));
  const listed = running.filter((e) => e.train.departs.getTime() <= until);
  const departures = confirmTrains(
    board,
    listed.filter((e) => e.serves === YES).map((e) => e.train),
    listed.filter((e) => e.serves === UNSURE).map((e) => e.train),
  );
  return { departures, complete: false, until, checked, cancelled };
}

// minutes since midnight of a "5:07 PM" style time
function clockMinutes(text) {
  const m = /^(\d{1,2}):(\d{2}) (AM|PM)$/.exec(text);
  if (!m) throw new Error(`unexpected schedule time: ${text}`);
  return (Number(m[1]) % 12) * 60 + Number(m[2]) + (m[3] === "PM" ? 12 * 60 : 0);
}

/**
 * Reads the BART wall clock at an instant.
 * @returns {{ day: number, minutes: number }} day is the calendar date as epoch ms of its UTC midnight
 */
function bartClock(date) {
  const parts = BART_CLOCK.formatToParts(date);
  const part = (type) => Number(parts.find((p) => p.type === type).value);
  return { day: Date.UTC(part("year"), part("month") - 1, part("day")), minutes: part("hour") * 60 + part("minute") };
}

/**
 * Finds when the BART wall clock reads a time on a calendar date. A time repeated when daylight time
 * ends is the earlier one, as in GTFS; a time skipped when it starts reads as standard time.
 * @param {number} day  calendar date as epoch ms of its UTC midnight
 * @returns {number} epoch ms
 */
function bartInstant(day, minutes) {
  const candidates = BART_UTC_OFFSETS_H.map((h) => day + (h * 60 + minutes) * MINUTE_MS);
  const matching = candidates.filter((t) => {
    const wallClock = bartClock(new Date(t));
    return wallClock.day === day && wallClock.minutes === minutes;
  });
  return matching.length > 0 ? Math.min(...matching) : candidates[0];
}

// calendar date of the service day in progress, as epoch ms of its UTC midnight
function serviceDay(date) {
  const { day, minutes } = bartClock(date);
  return minutes < SERVICE_DAY_START_MIN ? day - DAY_MS : day;
}

function scheduleRoute(lineName) {
  const m = /^ROUTE (\d+)$/.exec(lineName);
  const route = m && ROUTES.find((r) => r.number === Number(m[1]));
  if (!route) throw new Error(`unknown schedule line: ${lineName}`);
  return route;
}

// date of the service day in progress, as "MM/DD/YYYY"
function serviceDate() {
  const day = new Date(serviceDay(new Date()));
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(day.getUTCMonth() + 1)}/${pad(day.getUTCDate())}/${day.getUTCFullYear()}`;
}

/**
 * Converts timetable departures to trains of the current service day.
 * @param {{ line: string | null, time: string }[]} items  BART-local "h:mm AM" departure times
 * @returns {Train[]} trains from the current minute onward, in departure order
 */
function upcoming(items) {
  const day = serviceDay(new Date());
  const trains = [];
  for (const { line, time } of items) {
    const minutes = clockMinutes(time);
    const departs = new Date(bartInstant(minutes < SERVICE_DAY_START_MIN ? day + DAY_MS : day, minutes));
    if (minutesUntil(departs) >= 0) trains.push(scheduledTrain(departs, line));
  }
  return trains.sort((a, b) => a.departs - b.departs);
}

/**
 * A timetable of the service day in progress from the current minute on.
 * @typedef {DepartureList<Trip> & { direct: Leg[], transfers: Leg[][] }} Timetable
 *   departures leave without a transfer and transfers are trips of two legs, each in departure order;
 *   direct holds the legs of trips without a transfer, and direct and transfers also hold trips
 *   timetabled to leave shortly before now, whose first train may run late; when not complete, until is
 *   the last departure it holds
 */

/**
 * Loads the remaining timetable of a station for the service day in progress; after midnight the default
 * date of the API is the next service day.
 * @returns {Promise<Timetable>}
 */
async function loadSchedule(code) {
  const root = await loadLegacyApi(SCHEDULE_URL, { cmd: "stnsched", orig: code, date: serviceDate() });
  const items = root.station.item.map((i) => ({ line: scheduleRoute(i["@line"]).line, time: i["@origTime"] }));
  return {
    departures: upcoming(items).map(asTrip),
    direct: [],
    transfers: [],
    complete: true,
    until: Infinity,
    checked: 0,
  };
}

// instant of a trip planner time, BART-local "hh:mm AM" on an "MM/DD/YYYY" calendar date
function plannedInstant(date, time) {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(date);
  if (!m) throw new Error(`unexpected schedule date: ${date}`);
  return new Date(bartInstant(Date.UTC(Number(m[3]), Number(m[1]) - 1, Number(m[2])), clockMinutes(time)));
}

function plannedStation(code) {
  if (!STATIONS[code]) throw new Error(`unknown schedule station: ${code}`);
  return code;
}

/** @returns {Leg} */
function plannedLeg(leg) {
  const route = scheduleRoute(leg["@line"]);
  return {
    line: route.line,
    dir: route.dir,
    from: plannedStation(leg["@origin"]),
    to: plannedStation(leg["@destination"]),
    departs: plannedInstant(leg["@origTimeDate"], leg["@origTimeMin"]),
    arrives: plannedInstant(leg["@destTimeDate"], leg["@destTimeMin"]),
  };
}

// the planner gives the terminal of the Oakland Airport line as both ends of its leg, so a leg starting and
// ending at one station takes its other end from the adjacent leg
function withDistinctEnds(leg, i, legs) {
  if (leg.from !== leg.to) return leg;
  return i === 0 ? { ...leg, to: legs[1].from } : { ...leg, from: legs[i - 1].to };
}

/**
 * Loads the trips of the trip planner, without those needing more than one transfer.
 * @returns {Promise<Timetable>} complete when the plan reaches the next service day
 */
async function loadTripPlan(from, to) {
  const root = await loadLegacyApi(SCHEDULE_URL, {
    cmd: "depart",
    orig: from,
    dest: to,
    // the planner names its counts of trips before and after now b and a
    b: TRIPS_BEFORE,
    a: TRIPS_PLANNED,
  });
  const today = serviceDay(new Date());
  const planned = root.schedule.request.trip;
  // the planner gives a lone trip as an object
  const trips = (Array.isArray(planned) ? planned : [planned]).map((t) => t.leg.map(plannedLeg).map(withDistinctEnds));
  const current = trips.filter((legs) => serviceDay(legs[0].departs) === today);
  const ahead = current.filter((legs) => minutesUntil(legs[0].departs) >= 0);
  // a live train runs a leg up to LEG_MATCH_MIN.late after its timetable departure
  const catchable = current.filter((legs) => minutesUntil(legs[0].departs) >= -LEG_MATCH_MIN.late);
  const complete = current.length < trips.length;
  return {
    departures: ahead
      .filter((legs) => legs.length === 1)
      .map(([leg]) => ({ ...scheduledTrain(leg.departs, leg.line), arrives: leg.arrives, transfer: null })),
    direct: catchable.filter((legs) => legs.length === 1).map(([leg]) => leg),
    transfers: catchable.filter((legs) => legs.length === 2),
    complete,
    until: complete ? Infinity : Math.max(...current.map((legs) => legs[0].departs.getTime())),
    checked: 0,
  };
}

// varints are accumulated by multiplication because bitwise operators truncate to 32 bits
function readVarint(buf, pos) {
  let value = 0;
  let scale = 1;
  let byte;
  do {
    if (pos.i >= buf.length) throw new Error("truncated message");
    byte = buf[pos.i++];
    value += (byte & 0x7f) * scale;
    scale *= 128;
  } while (byte & 0x80);
  return value;
}

/**
 * Decodes one protobuf message from buf[start, end) into field number => values.
 * Varint fields decode to numbers and length-delimited fields to [start, end) byte ranges; fixed-width
 * fields are skipped.
 */
function decode(buf, start, end) {
  const fields = {};
  const pos = { i: start };
  while (pos.i < end) {
    const tag = readVarint(buf, pos);
    const wireType = tag % 8;
    let value;
    if (wireType === 0) {
      value = readVarint(buf, pos);
    } else if (wireType === 2) {
      const len = readVarint(buf, pos);
      if (pos.i + len > end) throw new Error("truncated message");
      value = [pos.i, pos.i + len];
      pos.i += len;
    } else if (wireType === 1) {
      pos.i += 8;
      continue;
    } else if (wireType === 5) {
      pos.i += 4;
      continue;
    } else {
      throw new Error(`unsupported protobuf wire type ${wireType}`);
    }
    (fields[Math.floor(tag / 8)] ??= []).push(value);
  }
  return fields;
}

// index of the last of stations in route order, or -1 when the route does not visit them in order
function lastIndexAlong(route, stations) {
  let i = -1;
  for (const s of stations) {
    i = route.stations.indexOf(s, i + 1);
    if (i < 0) return -1;
  }
  return i;
}

/**
 * Infers what the BART GTFS-RT feed leaves out of a feed trip from the routes its stops fit.
 * The feed lists the remaining departures of a run without its final stop, route, or direction, so
 * candidate routes contain the stops in order and continue past the last one. finals lists every
 * station that may be the omitted final stop.
 * @param {string[]} stations  the remaining stations of the run in order
 */
function describeFeedTrip(stations) {
  const along = ROUTES.map((r) => ({ r, i: lastIndexAlong(r, stations) })).filter(({ i }) => i >= 0);
  const candidates = along.filter(({ r, i }) => i < r.stations.length - 1);
  // a run ending at the end of every route it fits means the feed lists final stops
  if (along.length > 0 && candidates.length === 0) throw new Error("feed lists final stops");
  const finals = new Set();
  for (const { r, i } of candidates) {
    finals.add(r.stations[i + 1]);
    if (r.skips.includes(r.stations[i + 1]) && i + 2 < r.stations.length) finals.add(r.stations[i + 2]);
  }
  const lines = new Set(candidates.map(({ r }) => r.line));
  return { line: lines.size === 1 ? [...lines][0] : null, finals: [...finals] };
}

/**
 * One train run as the GTFS-RT feed lists it.
 * @typedef {{ stops: { station: string, departs: Date | null }[], line: string | null,
 *   finals: string[] }} FeedTrip
 */

/**
 * Loads every running trip in the GTFS-RT trip updates feed, without the stops it skips.
 * @returns {Promise<FeedTrip[]>}
 */
async function loadGtfsRt() {
  const req = new Request(GTFS_RT_URL);
  const buf = (await beforeDeadline(req.load())).getBytes();
  if (req.response.statusCode !== 200) throw new Error(`HTTP ${req.response.statusCode}`);
  const decodeRange = (range) => decode(buf, range[0], range[1]);
  const ascii = (range) => String.fromCharCode(...buf.slice(range[0], range[1]));

  const feed = decode(buf, 0, buf.length);
  const ageSec = Date.now() / 1000 - decodeRange(feed[FEED.header][0])[HEADER.timestamp][0];
  if (ageSec > MAX_FEED_AGE_S) throw new Error(`feed is ${Math.round(ageSec / 60)} min old`);

  const feedTrips = [];
  for (const entity of feed[FEED.entity] ?? []) {
    const tripUpdate = decodeRange(entity)[ENTITY.tripUpdate];
    if (!tripUpdate) continue;
    const update = decodeRange(tripUpdate[0]);
    const descriptor = update[TRIP_UPDATE.trip] ? decodeRange(update[TRIP_UPDATE.trip][0]) : {};
    const tripRelationship = descriptor[TRIP_DESCRIPTOR.scheduleRelationship]?.[0] ?? 0;
    if (TRIP_DROPPED.includes(tripRelationship)) continue;
    if (!TRIP_RUNS.includes(tripRelationship))
      throw new Error(`unexpected trip schedule relationship: ${tripRelationship}`);
    const stops = [];
    for (const range of update[TRIP_UPDATE.stopTimeUpdate] ?? []) {
      const u = decodeRange(range);
      const stopRelationship = u[STOP_TIME_UPDATE.scheduleRelationship]?.[0] ?? 0;
      if (stopRelationship === STOP_SKIPPED) continue;
      if (!STOP_SERVED.includes(stopRelationship))
        throw new Error(`unexpected stop schedule relationship: ${stopRelationship}`);
      if (!u[STOP_TIME_UPDATE.stopId]) throw new Error("stop time update without stop_id");
      const platform = ascii(u[STOP_TIME_UPDATE.stopId][0]);
      const station = STATION_OF_PLATFORM[platform];
      if (!station) throw new Error(`unknown platform: ${platform}`);
      const time = (kind) => (u[kind] ? decodeRange(u[kind][0])[STOP_TIME_EVENT.time]?.[0] : undefined);
      const arrives = time(STOP_TIME_UPDATE.arrival);
      let departs = time(STOP_TIME_UPDATE.departure) ?? arrives;
      // BART dates a departure just after midnight on the previous day when its arrival is before midnight
      if (arrives && departs < arrives - DAY_S / 2) departs += DAY_S;
      const stop = { station, departs: departs ? new Date(departs * 1000) : null };
      // consecutive platforms of one station, as at SFO, are one stop that departs from the last
      if (stops.length > 0 && stops[stops.length - 1].station === station) stops[stops.length - 1] = stop;
      else stops.push(stop);
    }
    if (stops.length > 0) feedTrips.push({ stops, ...describeFeedTrip(stops.map((s) => s.station)) });
  }
  return feedTrips;
}

// YES when a feed trip that departs the board origin at stop index i reaches its destination, UNSURE when it may
function feedTripServes(feedTrip, i, board) {
  if (!board.to) return YES;
  if (feedTrip.stops.slice(i + 1).some((s) => s.station === board.to)) return YES;
  if (!feedTrip.finals.includes(board.to)) return NO;
  return feedTrip.finals.length === 1 ? YES : UNSURE;
}

/**
 * Returns the trains of the board within the feed horizon, since a train after the horizon may be missing.
 * @returns {Train[]}
 */
function trainsFromGtfsRt(feedTrips, board) {
  const now = Date.now();
  const horizon = now + GTFS_RT_HORIZON_MIN * MINUTE_MS;
  const confirmed = [];
  const unsure = [];
  for (const feedTrip of feedTrips) {
    const i = feedTrip.stops.findIndex((s) => s.station === board.from);
    const departs = i >= 0 ? feedTrip.stops[i].departs : null;
    if (!departs || departs.getTime() < now || departs.getTime() > horizon) continue;
    const train = liveTrain(Math.round((departs.getTime() - now) / MINUTE_MS), departs, feedTrip.line);
    const serves = feedTripServes(feedTrip, i, board);
    if (serves === YES) confirmed.push(train);
    if (serves === UNSURE) unsure.push(train);
  }
  return confirmTrains(board, confirmed, unsure);
}

/**
 * Memoizes each live source request so boards sharing a station or the feed fetch it once.
 * @returns {{ etd: (code: string) => Promise, gtfsRt: () => Promise,
 *   live: (from: string, to: string) => Promise }}
 */
function sources() {
  const etd = {};
  const live = {};
  let feed;
  const src = {
    etd: (code) => (etd[code] ??= loadEtd(code)),
    gtfsRt: () => (feed ??= loadGtfsRt()),
    live: (from, to) => (live[`${from}-${to}`] ??= liveTrains({ id: `${from}-${to}`, from, to, shown: Infinity }, src)),
  };
  return src;
}

// feed trains that no ETD train of their line runs; each ETD train runs at most one, the nearest
function omittedTrains(etdTrains, feedTrains) {
  const unpaired = [...etdTrains];
  return feedTrains.filter((t) => {
    const apart = (e) => Math.abs(e.departs - t.departs);
    const same = unpaired.filter(
      (e) =>
        (t.line === null || e.line === t.line || e.line === UNSCHEDULED.line) && apart(e) <= SAME_TRAIN_MIN * MINUTE_MS,
    );
    if (same.length === 0) return true;
    unpaired.splice(unpaired.indexOf(same.reduce((x, y) => (apart(y) < apart(x) ? y : x))), 1);
    return false;
  });
}

/**
 * Loads the live trains of a board: those from ETD, with the GTFS-RT trains ETD omits, or either source alone when
 * the other fails or has no train for the board. Source failures are logged rather than thrown.
 * @returns {Promise<DepartureList<Train> | null>} null when no live source has data
 */
async function liveTrains(board, src) {
  const now = Date.now();
  // the feed holds every departure within its horizon
  const horizon = now + GTFS_RT_HORIZON_MIN * MINUTE_MS;
  // first failure of each source, logged in this order once both are known
  const failures = { ETD: null, "GTFS-RT": null };
  const attempt = async (source, step) => {
    try {
      return await step();
    } catch (e) {
      failures[source] ??= e.message;
      return null;
    }
  };
  const [estimates, feedTrips] = await Promise.all([
    attempt("ETD", () => src.etd(board.from)),
    attempt("GTFS-RT", () => src.gtfsRt()),
  ]);
  const feed = feedTrips && (await attempt("GTFS-RT", () => trainsFromGtfsRt(feedTrips, board)));
  const etd =
    estimates &&
    (await attempt("ETD", () => trainsFromEtd(estimates, board, feed ? horizon : null, feed ? horizon : now)));
  for (const [source, message] of Object.entries(failures))
    if (message) console.warn(`${board.id} ${source}: ${message}`);
  if (!etd) return feed && { departures: feed, complete: false, until: horizon, checked: horizon };
  if (!feed) return etd;
  // a train ETD reports cancelled stays off the board though the feed still lists it
  const departures = [...etd.departures, ...omittedTrains([...etd.departures, ...etd.cancelled], feed)].sort(
    (a, b) => a.departs - b.departs,
  );
  return { ...etd, departures, until: Math.max(etd.until, horizon) };
}

/**
 * Finds the live train that runs a planned leg: the first of its line within the match window. A train
 * of unknown line there could be it, so the leg is then unknown.
 * @returns {Promise<LegRun>} NOT_RUNNING when the live trains cover the window without it
 */
async function runningLeg(leg, src) {
  const unknown = { status: UNKNOWN, train: null };
  if (UNTRACKED_LINES.includes(leg.line)) return unknown;
  const live = await src.live(leg.from, leg.to);
  if (!live) return unknown;
  const early = leg.departs.getTime() - LEG_MATCH_MIN.early * MINUTE_MS;
  const late = leg.departs.getTime() + LEG_MATCH_MIN.late * MINUTE_MS;
  const first = live.departures.find(
    (t) => (t.line === leg.line || t.line === null) && t.departs >= early && t.departs <= late,
  );
  if (first) return first.line === null ? unknown : { status: RUNNING, train: first };
  // ETD may omit a train waiting at the end of its line to leave
  const route = ROUTES.find((r) => r.line === leg.line && r.dir === leg.dir);
  if (route.stations[0] === leg.from) return unknown;
  // only the feed rules a train out, and only a listed train after the window shows the list reaches past it
  return late <= live.checked && live.departures.some((t) => t.departs > late)
    ? { status: NOT_RUNNING, train: null }
    : unknown;
}

function isGrandMeet(second) {
  const { minutes } = bartClock(second.departs);
  return (
    second.from === GRAND_MEET.station &&
    minutes >= clockMinutes(GRAND_MEET.first) &&
    minutes <= clockMinutes(GRAND_MEET.last)
  );
}

// whether BART times the connection between two legs, holding the connecting train for the first
function isTimed(first, second) {
  return first.dir === second.dir && TIMED_TRANSFERS.some((t) => t.station === second.from && t.dir === first.dir);
}

/**
 * Returns a planned trip with a transfer, or null when the live trains miss the connection or do not
 * run. A trip is scheduled unless live trains run both legs, and keeps its planned connection when
 * neither leg is known.
 * @param {LegRun} firstRun
 * @param {LegRun} connectingRun
 * @returns {Trip | null}
 */
function transferTrip([first, second], firstRun, connectingRun) {
  if (firstRun.status === NOT_RUNNING || connectingRun.status === NOT_RUNNING) return null;
  const firstTrain = firstRun.train;
  const connectingTrain = connectingRun.train;
  // a trip timetabled to leave before now leaves only on a live first train yet to depart
  if (!firstTrain && minutesUntil(first.departs) < 0) return null;
  // live departures carry the seconds of the run, so delays count in the whole minutes of the plan
  const lateness = (train, leg) => (train ? Math.round((train.departs - leg.departs) / MINUTE_MS) * MINUTE_MS : 0);
  const reaches = first.arrives.getTime() + lateness(firstTrain, first);
  const leaves = second.departs.getTime() + lateness(connectingTrain, second);
  // a timed connection waits for the first train unless live times show it leaving first
  const held = isGrandMeet(second) || (!connectingTrain && isTimed(first, second));
  const holds = (!firstTrain && !connectingTrain) || held || leaves - reaches >= TRANSFER_SLACK_MIN * MINUTE_MS;
  if (!holds) return null;
  const train = firstTrain ?? scheduledTrain(first.departs, first.line);
  // a connecting train held for the first leaves once the first train is in
  const arrives = new Date(Math.max(leaves, reaches) + (second.arrives - second.departs));
  return {
    ...train,
    arrives,
    scheduled: !firstTrain || !connectingTrain,
    transfer: { station: second.from, line: second.line },
  };
}

/**
 * Shows a live train that starts its run at the board origin at its timetable departure when that is
 * earlier, since BART predicts departures there minutes late while trains leave close to the timetable.
 * The train runs the latest planned leg of its line within the leg matching window, so a train is never
 * moved before a later leg it may run.
 * @param {Leg[]} legs  first legs of the planned trips of the board
 */
function startDeparture(train, legs) {
  const at = train.departs.getTime();
  const fits = (l) =>
    l.departs.getTime() >= at - LEG_MATCH_MIN.late * MINUTE_MS &&
    l.departs.getTime() <= at + LEG_MATCH_MIN.early * MINUTE_MS;
  const leg = legs
    .filter((l) => l.line === train.line && fits(l))
    .reduce((a, b) => (a && a.departs >= b.departs ? a : b), null);
  const starts =
    leg && ROUTES.some((r) => r.line === leg.line && r.stations[0] === leg.from && r.stations.includes(leg.to));
  if (!starts || leg.departs >= train.departs) return train;
  return { ...train, departs: leg.departs, minutes: Math.max(0, minutesUntil(leg.departs)) };
}

// whether trip b leaves no earlier than trip a and arrives sooner, or as soon by leaving later or without a transfer
function beats(b, a) {
  if (b === a || b.departs < a.departs || b.arrives > a.arrives) return false;
  if (b.arrives < a.arrives || b.departs > a.departs) return true;
  return !b.transfer && a.transfer !== null;
}

// trips in departure order without those another trip beats, so arrivals increase down the list
function fastest(trips) {
  return trips
    .filter((a) => !trips.some((b) => beats(b, a)))
    .sort((a, b) => a.departs - b.departs || a.arrives - b.arrives);
}

/**
 * Loads the direct trains of a board from the live trains, or from the timetable when they have none. Both
 * live sources are skipped for a board an untracked line serves. Source failures are logged rather than
 * thrown.
 * @param {() => Promise<Timetable | null>} timetable
 * @returns {Promise<DepartureList<Trip> | null>} null when no source has data
 */
async function loadDirect(board, src, timetable) {
  if (board.to && !hasDirectLine(board)) return { departures: [], complete: true, until: Infinity, checked: 0 };
  // the live trains show that a timetable train before checked that they lack does not run
  let checked = 0;
  if (!isUntracked(board)) {
    const live = await liveTrains(board, src);
    if (live && live.departures.length > 0) return { ...live, departures: live.departures.map(asTrip) };
    if (live) checked = live.checked;
  }
  const table = await timetable();
  if (!table) return null;
  const later = table.departures.filter((t) => t.departs > checked);
  // a trip plan with transfers covers the board through its last trip though it lists no direct train
  if (later.length > 0 || table.complete || table.transfers.length > 0) {
    return { departures: later, complete: table.complete, until: table.until, checked };
  }
  // a trip plan that ends before the live trains rule out its trains leaves later trains unknown
  console.warn(`${board.id} schedule: no trains in the trip plan after live data ends`);
  return null;
}

/**
 * Loads the direct trains of a board, then for a commute merges in the timetable trips with a transfer
 * that the live trains still make, keeping only the fastest trips up to where any list may miss one.
 * Without the timetable a commute shows direct trains alone, and has no data when no line is direct.
 * @param {() => Promise<Timetable | null>} timetable
 * @returns {Promise<DepartureList<Trip> | null>} null when no source has data
 */
async function loadBoard(board, src, timetable) {
  const direct = await loadDirect(board, src, timetable);
  if (!direct || !board.to) return direct;
  const table = await timetable();
  if (!table) return hasDirectLine(board) ? direct : null;
  const starts = [...table.direct, ...table.transfers.map(([leg]) => leg)];
  const displayed = (t) => (t.scheduled ? t : startDeparture(t, starts));
  if (table.transfers.length === 0 && hasDirectLine(board)) {
    return { ...direct, departures: direct.departures.map(displayed) };
  }
  // trains between two stations share track, so the shortest direct trip times every line
  const runs = table.departures.map((t) => t.arrives - t.departs);
  if (direct.departures.length > 0 && runs.length === 0) {
    console.warn(`${board.id} schedule: no direct trip in the trip plan`);
    return { ...direct, departures: direct.departures.map(displayed) };
  }
  // a direct train after the trip plan ends could be beaten by a trip it omits, while a trip from the plan
  // stays when its first train runs late past the last departure of the plan
  const until = Math.min(direct.until, table.until);
  // arrivals are estimated from live departures before any is shown at its timetable time
  const trips = direct.departures
    .filter((t) => t.departs <= until)
    .map((t) => ({ ...t, arrives: new Date(t.departs.getTime() + Math.min(...runs)) }))
    .map(displayed);
  for (const legs of table.transfers) {
    const [firstRun, connectingRun] = await Promise.all(legs.map((leg) => runningLeg(leg, src)));
    const trip = transferTrip(legs, firstRun, connectingRun);
    if (trip && trip.departs <= direct.until) trips.push(firstRun.train ? startDeparture(trip, starts) : trip);
  }
  return { ...direct, departures: fastest(trips), until, complete: direct.complete && table.complete };
}

/**
 * Whether the timetable has no departure after a live train, which makes it the last of the service
 * day. A prediction and the timetable minute of one train differ by less than a minute.
 */
async function isLastTrain(train, timetable) {
  const table = await timetable();
  if (!table?.complete) return false;
  const departures = [...table.departures.map((t) => t.departs), ...table.transfers.map(([leg]) => leg.departs)];
  return !departures.some((d) => d - train.departs >= MINUTE_MS);
}

/** @returns {Promise<LoadedBoard[]>} */
async function loadBoards(boards) {
  const src = sources();
  return Promise.all(
    boards.map(async (board) => {
      let table;
      // timetable of the board, loaded at most once and logged when it fails; null without data
      const timetable = () =>
        (table ??= (board.to ? loadTripPlan(board.from, board.to) : loadSchedule(board.from)).catch((e) => {
          console.warn(`${board.id} schedule: ${e.message}`);
          return null;
        }));
      // a commute always uses its trip plan, so it loads alongside the live trains
      if (board.to) timetable();
      const list = await loadBoard(board, src, timetable);
      if (!list) return { board, trips: null, last: false };
      const { departures: trips, complete } = list;
      const last =
        trips.length > 0 &&
        trips.length <= board.shown &&
        (complete || (await isLastTrain(trips[trips.length - 1], timetable)));
      return { board, trips: trips.slice(0, board.shown), last };
    }),
  );
}

const dynamic = (light, dark) => Color.dynamic(new Color(light), new Color(dark));
const BG_LIGHT = "#ffffff";
const BG_DARK = "#1c1c1e";
const BG = dynamic(BG_LIGHT, BG_DARK);
const FG = dynamic("#000000", "#ffffff");
const DIM = dynamic("#6c6c70", "#8e8e93");
const DIVIDER = dynamic("#c6c6c8", "#38383a");
// the Lock Screen applies its own tint, so its text does not follow the appearance
const LOCK_FG = Color.white();
const LOCK_DIM = new Color("#8e8e93");
// line dots on the light background, where the colors for the dark background would wash out
const LIGHT_LINE_COLORS = { GREY: "#8a97a0", ORANGE: "#e8830c", WHITE: "#1c1c1e", YELLOW: "#dcae00" };
const LINE_LETTERS = { BLUE: "B", GREEN: "G", GREY: "*", ORANGE: "O", RED: "R", YELLOW: "Y" };

const NO_DATA = "No data";
const NO_DEPARTURE = "--";
const LAST_TRAIN = "last train";

// 12-hour wall clock times in every locale
const clockFormat = new DateFormatter();
clockFormat.dateFormat = "h:mm a";
clockFormat.locale = "en";

function style(el, font, color) {
  el.font = font;
  el.textColor = color;
  el.lineLimit = 1;
  el.minimumScaleFactor = 0.5;
  return el;
}

function addLabel(stack, str, font, color = FG) {
  return style(stack.addText(str), font, color);
}

const TRAM_SYMBOL = "tram.fill";
const TRANSFER_SYMBOL = "arrow.triangle.branch";
const LAST_TRAIN_SYMBOL = "flag.checkered";
const REFRESH_SYMBOL = "arrow.clockwise";
const UPDATED_LABEL = "updated";
const AGO_LABEL = "ago";
const APP_TITLE = "BART Commuter";
// gap after a symbol that labels the text following it
const SYMBOL_GAP = 2;

function addSymbol(stack, name, width, height, color) {
  const image = stack.addImage(SFSymbol.named(name).image);
  image.imageSize = new Size(width, height);
  image.tintColor = color;
  return image;
}

// a tram symbol and a gap before a title
function addTram(stack, size, color = LOCK_FG) {
  addSymbol(stack, TRAM_SYMBOL, size, size, color);
  stack.addSpacer(Math.round(size * 0.3));
}

// a color mixed with a background, from the background alone at 0 to the color alone at 1
function fadedHex(hex, background, strength) {
  const channel = (color, i) => parseInt(color.slice(1 + 2 * i, 3 + 2 * i), 16);
  const mixed = [0, 1, 2].map((i) =>
    Math.round(channel(background, i) + (channel(hex, i) - channel(background, i)) * strength),
  );
  return `#${mixed.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

// line color marker; gray when the source does not identify the line
function addLineDot(stack, line, size, strength = 1) {
  const dot = stack.addStack();
  dot.size = new Size(size, size);
  dot.cornerRadius = size / 2;
  const hex = line === UNSCHEDULED.line ? UNSCHEDULED.color : LINE_COLORS[line];
  dot.backgroundColor = hex
    ? dynamic(fadedHex(LIGHT_LINE_COLORS[line] ?? hex, BG_LIGHT, strength), fadedHex(hex, BG_DARK, strength))
    : DIM;
  return dot;
}

function addRow(stack) {
  const row = stack.addStack();
  row.centerAlignContent();
  return row;
}

const DEPARTURE_DOT_GAP = 6;

function departureDotSize(size) {
  return Math.round(size * 0.3);
}

// width of the line dot of a departure time and the gap after it
function departureIndent(size) {
  return departureDotSize(size) + DEPARTURE_DOT_GAP;
}

// descent of SF Pro as a fraction of its size
const FONT_DESCENT = 0.241;

// a clock time with a smaller AM or PM on its baseline; stacks align by line box, so the period is raised
function addClock(row, date, size, periodSize, color = FG) {
  const [time, period] = clockFormat.string(date).split(" ");
  const box = row.addStack();
  box.bottomAlignContent();
  addLabel(box, time, Font.boldRoundedSystemFont(size), color);
  const raised = box.addStack();
  raised.layoutVertically();
  addLabel(raised, ` ${period}`, Font.boldRoundedSystemFont(periodSize), color);
  raised.addSpacer(Math.round((size - periodSize) * FONT_DESCENT));
}

// a departure time in large type after its line dot
function addDeparture(row, train, size) {
  addLineDot(row, train.line, departureDotSize(size));
  row.addSpacer(DEPARTURE_DOT_GAP);
  addClock(row, train.departs, size, Math.round(size / 2));
}

// the refresh symbol and how long ago this data was loaded, with the word updated when labeled
function addUpdated(stack, color, size, labeled = true) {
  const updated = addRow(stack);
  const font = Font.systemFont(size);
  addSymbol(updated, REFRESH_SYMBOL, size, size, color);
  updated.addSpacer(SYMBOL_GAP);
  if (labeled) {
    addLabel(updated, UPDATED_LABEL, font, color);
    updated.addSpacer(SYMBOL_GAP);
  }
  // iOS keeps an offset date current between reloads, and without a box it fills the row, pushing away the word after it
  const box = updated.addStack();
  // SF tracks tighter as it grows, so a width scaled from a smaller size still holds the text
  box.size = new Size((AGE_WIDTH * size) / UPDATED_SIZE, 0);
  const age = box.addDate(new Date());
  age.applyOffsetStyle();
  age.rightAlignText();
  style(age, font, color);
  updated.addSpacer(SYMBOL_GAP);
  addLabel(updated, AGO_LABEL, font, color);
}

// a full-width rule; the flexible spacer stretches its zero-width stack across the widget
function addDivider(root) {
  const bar = root.addStack();
  bar.size = new Size(0, 1);
  bar.backgroundColor = DIVIDER;
  bar.addSpacer();
}

const APP_HEADER_GAP = 3;

/**
 * Adds the app name above a rule.
 * @param {number} [updatedSize]  font size of an updated row at the right of the header, when it has one
 */
function addAppHeader(root, size, updatedSize) {
  const header = addRow(root);
  addTram(header, size, DIM);
  addLabel(header, APP_TITLE, Font.semiboldSystemFont(size), DIM);
  header.addSpacer();
  if (updatedSize) addUpdated(header, DIM, updatedSize);
  root.addSpacer(APP_HEADER_GAP);
  addDivider(root);
  root.addSpacer(APP_HEADER_GAP);
}

// gap between the columns of a medium or large widget
const COLUMN_GAP = 8;
// row width in points on a 6.1-inch iPhone: a 158pt small widget inside its 12pt side padding, a
// 160pt rectangular one, and a 338pt large one inside its 14pt side padding
const ROW_WIDTH = { small: 134, accessoryRectangular: 160, large: 310 };
// text fits when shrunk to no less than this fraction of its size
const MIN_TEXT_SCALE = 0.8;

/**
 * Adds a bold origin, then the destination of a commute. Full station names give way to codes, the
 * destination first, when the heading would not fit the width.
 */
function addHeading(stack, board, width, fg = FG, dim = DIM, kind = "compact") {
  const heading = HEADINGS[kind];
  const origin = Font.boldSystemFont(heading.origin);
  if (!board.to) {
    addLabel(stack, board.title, origin, fg);
    return;
  }
  const fits = ([from, to]) =>
    (HEADING_WIDTHS[board.from][from][heading.column] +
      heading.arrow +
      HEADING_WIDTHS[board.to][to][heading.column + 1]) *
      MIN_TEXT_SCALE <=
    width;
  const [from, to] = [
    ["name", "name"],
    ["name", "code"],
  ].find(fits) ?? ["code", "code"];
  const label = (code, form) => (form === "name" ? STATIONS[code].name : code);
  addLabel(stack, label(board.from, from), origin, fg);
  addLabel(stack, ` \u2192 ${label(board.to, to)}`, Font.systemFont(heading.destination), dim);
}

// line dots of later trains are quieter than the dot of the next departure
const LATER_DOT_STRENGTH = 0.5;
const TRANSFER_DOT = 6;
const TRANSFER_GAP = 4;

/**
 * Adds the transfer symbol, the dot of the connecting line and the transfer station.
 * @param {"small" | "columns" | "large" | "list"} key  the transfer size
 */
function addTransfer(row, transfer, key) {
  const size = TRANSFER_SIZES[key];
  addSymbol(row, TRANSFER_SYMBOL, size, size, DIM);
  row.addSpacer(TRANSFER_GAP);
  addLineDot(row, transfer.line, TRANSFER_DOT, key === "list" ? LATER_DOT_STRENGTH : 1);
  row.addSpacer(TRANSFER_GAP);
  addLabel(
    row,
    STATIONS[transfer.station].name,
    key === "list" ? Font.italicSystemFont(size) : Font.systemFont(size),
    DIM,
  );
}

function laterClock(train) {
  return clockFormat.string(train.departs).toLowerCase();
}

// the time of a later train without the space before am or pm
function compactClock(train) {
  return laterClock(train).replace(/\s/, "");
}

function addLastTrainFlag(row, size, color = DIM) {
  addSymbol(row, LAST_TRAIN_SYMBOL, size, size, color);
}

// the flag and the words that mark the last train
function addLastTrain(row, size, color = DIM) {
  addLastTrainFlag(row, size, color);
  row.addSpacer(SYMBOL_GAP);
  addLabel(row, LAST_TRAIN, Font.systemFont(size), color);
}

/**
 * Adds a row marking the last train.
 * @param {number} indent  points before the flag
 */
function addLastTrainRow(col, size, indent) {
  const row = addRow(col);
  row.addSpacer(indent);
  addLastTrain(row, size);
  row.addSpacer();
}

// a transfer on its own row, indented by the given points
function addTransferRow(col, transfer, key, indent) {
  const row = addRow(col);
  row.addSpacer(indent);
  addTransfer(row, transfer, key);
}

/**
 * Adds the transfer and last-train rows of a next departure, each starting under its time.
 * @param {"small" | "large"} key  the transfer size
 * @param {number} size  font size of the departure time
 */
function addDepartureDetails(col, loaded, key, size, lastSize) {
  const { transfer } = loaded.trips[0];
  const indent = departureIndent(size);
  if (transfer) addTransferRow(col, transfer, key, indent);
  if (loaded.last && loaded.trips.length === 1) addLastTrainRow(col, lastSize, indent);
}

/**
 * Adds later departures as line dots and clock times.
 * @param {boolean} last  whether the final one is the last train
 */
function addLaterTrains(row, trips, last, size) {
  trips.forEach((t, i) => {
    if (i > 0) row.addSpacer(6);
    addLineDot(row, t.line, 6, LATER_DOT_STRENGTH);
    row.addSpacer(3);
    addLabel(row, compactClock(t), Font.italicSystemFont(size), DIM);
    if (last && i === trips.length - 1) {
      row.addSpacer(SYMBOL_GAP);
      addLastTrainFlag(row, size);
    }
  });
}

const NO_DATA_SYMBOL = "icloud.slash";
const NO_TRAINS_SYMBOL = "nosign";

// a symbol for a board without a next departure where its departure time would be, at the font size of that time
function addEmptyBoard(row, loaded, size) {
  const symbol = Math.round(size * 0.6);
  if (loaded.trips) addSymbol(row, NO_TRAINS_SYMBOL, symbol, symbol, FG);
  else addSymbol(row, NO_DATA_SYMBOL, symbol, symbol, DIM);
}

// gap between the trips of a small widget, the most that fits when both trips have a transfer row
const SMALL_TRIP_GAP = 4;
const SMALL_LATER_GAP = 2;
// a heading uses the first of its kinds that shows every full name unshrunk, then the compact kind
const SMALL_ONE_STATION = { departure: 30, later: 11, headings: ["spacious", "comfortable"] };
// sizes of a two-trip widget by the number of trips with a transfer row; every trip has the same sizes
const SMALL_TWO_TRIPS = [
  { departure: 26, later: 11, headings: ["spacious", "comfortable"] },
  { departure: 24, later: 10, headings: ["comfortable"] },
  { departure: 24, later: 10, headings: [] },
];

function smallSizes(loadedBoards) {
  if (loadedBoards.length === 1) return SMALL_ONE_STATION;
  return SMALL_TWO_TRIPS[loadedBoards.filter((loaded) => loaded.trips?.[0]?.transfer).length];
}

// whether the full station names of a heading fit the width without shrinking
function namesFit(board, width, kind) {
  const { column, arrow } = HEADINGS[kind];
  const origin = HEADING_WIDTHS[board.from].name[column];
  return (board.to ? origin + arrow + HEADING_WIDTHS[board.to].name[column + 1] : origin) <= width;
}

// two trips of four rows nearly fill the height of a small widget
function buildSmall(root, loadedBoards) {
  const sizes = smallSizes(loadedBoards);
  const heading =
    sizes.headings.find((kind) => loadedBoards.every((l) => namesFit(l.board, ROW_WIDTH.small, kind))) ?? "compact";
  root.addSpacer();
  loadedBoards.forEach((loaded, i) => {
    if (i > 0) {
      root.addSpacer(SMALL_TRIP_GAP);
      addDivider(root);
      root.addSpacer(SMALL_TRIP_GAP);
    }
    addHeading(addRow(root), loaded.board, ROW_WIDTH.small, FG, DIM, heading);
    const next = loaded.trips?.[0];
    if (!next) {
      addEmptyBoard(addRow(root), loaded, sizes.departure);
      return;
    }
    addDeparture(addRow(root), next, sizes.departure);
    addDepartureDetails(root, loaded, "small", sizes.departure, sizes.later);
    if (loaded.trips.length > 1) {
      root.addSpacer(SMALL_LATER_GAP);
      addLaterTrains(addRow(root), loaded.trips.slice(1), loaded.last, sizes.later);
    }
  });
  root.addSpacer();
}

const LIST_DOT = 6;
const LIST_GAP = 4;

const LIST_INDENT = 8;
const LIST_TRAIN_GAP = 3;
// rows the list of a stacked trip can fill
const LIST_ROWS = 6;

/**
 * Adds later departures one per row, as many as fit, each with its transfer on a row below it.
 * @param {boolean} last  whether the final one is the last train
 */
function addTrainList(col, trips, last) {
  const size = TRANSFER_SIZES.list;
  const timeIndent = LIST_INDENT + LIST_DOT + LIST_GAP;
  let rows = 0;
  for (const [i, t] of trips.entries()) {
    rows += t.transfer ? 2 : 1;
    if (rows > LIST_ROWS) break;
    if (i > 0) col.addSpacer(LIST_TRAIN_GAP);
    const row = addRow(col);
    row.addSpacer(LIST_INDENT);
    addLineDot(row, t.line, LIST_DOT, LATER_DOT_STRENGTH);
    row.addSpacer(LIST_GAP);
    addLabel(row, laterClock(t), Font.italicSystemFont(size), DIM);
    if (last && i === trips.length - 1) {
      row.addSpacer(LIST_GAP);
      addLastTrainFlag(row, size);
    }
    row.addSpacer();
    if (t.transfer) {
      const below = addRow(col);
      below.addSpacer(timeIndent);
      addTransfer(below, t.transfer, "list");
      below.addSpacer();
    }
  }
}

// gap above the row of later trains or last-train words in a column
const COLUMN_LATER_GAP = 8;
// fixed so that row is level across columns whichever it holds
const COLUMN_LATER_HEIGHT = 15;
const COLUMN_LATER_SIZE = 12;

/**
 * Adds the rows of a column below its next departure, level with those of the other column.
 * @param {boolean} transferRow  whether any column has a transfer row
 */
function addColumnDetails(col, loaded, transferRow) {
  const [{ transfer }, ...later] = loaded.trips;
  const indent = departureIndent(DEPARTURE_SIZE);
  if (transfer) addTransferRow(col, transfer, "columns", indent);
  else if (transferRow) addLabel(addRow(col), " ", Font.systemFont(TRANSFER_SIZES.columns));
  if (later.length === 0 && !loaded.last) return;
  col.addSpacer(COLUMN_LATER_GAP);
  const row = addRow(col);
  row.size = new Size(0, COLUMN_LATER_HEIGHT);
  if (later.length > 0) {
    addLaterTrains(row, later, loaded.last, COLUMN_LATER_SIZE);
  } else {
    row.addSpacer(indent);
    addLastTrain(row, COLUMN_LATER_SIZE);
  }
  row.addSpacer();
}

// the name and next train of a board, or the symbol for having none
function addColumn(col, loaded, transferRow) {
  const title = addRow(col);
  addLabel(title, loaded.board.title, Font.semiboldSystemFont(17));
  // a flexible row in each column gives side-by-side columns equal widths
  title.addSpacer();
  if (loaded.board.subtitle) addLabel(addRow(col), loaded.board.subtitle, Font.systemFont(14), DIM);
  col.addSpacer(6);
  const next = loaded.trips?.[0];
  if (!next) {
    addEmptyBoard(addRow(col), loaded, DEPARTURE_SIZE);
    return;
  }
  addDeparture(addRow(col), next, DEPARTURE_SIZE);
  addColumnDetails(col, loaded, transferRow);
}

function buildColumns(root, loadedBoards) {
  addAppHeader(root, 11, UPDATED_SIZE);
  root.addSpacer();
  const transferRow = loadedBoards.some((loaded) => loaded.trips?.[0]?.transfer);
  const row = root.addStack();
  loadedBoards.forEach((loaded, i) => {
    if (i > 0) row.addSpacer(COLUMN_GAP);
    const col = row.addStack();
    col.layoutVertically();
    addColumn(col, loaded, transferRow);
  });
  root.addSpacer();
}

const STACKED_SIZES = { departure: 33, header: 14, updated: 12 };

// what a stacked trip shows below its station name at the left
function addStackedNextTrain(col, loaded) {
  const next = loaded.trips?.[0];
  const row = addRow(col);
  if (next) addDeparture(row, next, STACKED_SIZES.departure);
  else addEmptyBoard(row, loaded, STACKED_SIZES.departure);
  // a flexible row in each column gives side-by-side columns equal widths
  row.addSpacer();
  if (next) addDepartureDetails(col, loaded, "large", STACKED_SIZES.departure, TRANSFER_SIZES.list);
}

// trips one above the other, each with its route across the top, its next train at the left and its later trains from the center
function buildStacked(root, loadedBoards) {
  addAppHeader(root, STACKED_SIZES.header, STACKED_SIZES.updated);
  loadedBoards.forEach((loaded, i) => {
    root.addSpacer();
    if (i > 0) {
      addDivider(root);
      root.addSpacer();
    }
    const title = addRow(root);
    addHeading(title, loaded.board, ROW_WIDTH.large, FG, DIM, "large");
    title.addSpacer();
    const row = root.addStack();
    const next = row.addStack();
    next.layoutVertically();
    addStackedNextTrain(next, loaded);
    row.addSpacer(COLUMN_GAP);
    const later = row.addStack();
    later.layoutVertically();
    if (loaded.trips?.length > 1) {
      later.addSpacer(8);
      addTrainList(later, loaded.trips.slice(1), loaded.last);
    }
  });
  root.addSpacer();
}

// the Lock Screen is drawn in one tint, so a line is a letter rather than a color
function lineLetter(train) {
  return `(${train.line === UNSCHEDULED.line ? UNSCHEDULED.letter : LINE_LETTERS[train.line]}) `;
}

// a title and two rows per trip fill the height of the widget
function buildRectangular(root, loadedBoards) {
  const title = addRow(root);
  addTram(title, 11);
  addLabel(title, "BART", Font.semiboldSystemFont(11), LOCK_FG);
  title.addSpacer(6);
  addUpdated(title, LOCK_DIM, UPDATED_SIZE, false);
  for (const loaded of loadedBoards) {
    addHeading(addRow(root), loaded.board, ROW_WIDTH.accessoryRectangular, LOCK_FG, LOCK_DIM);
    const row = addRow(root);
    row.addSpacer(6);
    const next = loaded.trips?.[0];
    if (!next) {
      addLabel(row, loaded.trips ? NO_DEPARTURE : NO_DATA, Font.mediumSystemFont(12), LOCK_FG);
      continue;
    }
    if (next.line) addLabel(row, lineLetter(next), Font.semiboldSystemFont(10), LOCK_FG);
    addClock(row, next.departs, 13, 9, LOCK_FG);
    const later = loaded.trips[1];
    if (later) {
      addLabel(
        row,
        `  ${later.line ? lineLetter(later) : ""}${compactClock(later)}`,
        Font.italicSystemFont(10),
        LOCK_DIM,
      );
      if (loaded.last && loaded.trips.length === 2) {
        row.addSpacer(SYMBOL_GAP);
        addLastTrainFlag(row, 10, LOCK_DIM);
      }
    } else if (loaded.last) {
      row.addSpacer(6);
      addLastTrain(row, 10, LOCK_FG);
    }
  }
}

// minutes into the day from which a circular widget shows the return trip of the commute, 1 PM
const RETURN_TRIP_MIN = 13 * 60;

function buildCircular(widget, loadedBoards) {
  widget.addAccessoryWidgetBackground = true;
  const { board, trips, last } =
    loadedBoards[bartClock(new Date()).minutes < RETURN_TRIP_MIN ? 0 : loadedBoards.length - 1];
  if (!trips?.[0]) {
    addLabel(widget, NO_DEPARTURE, Font.boldRoundedSystemFont(16), LOCK_FG);
    return;
  }
  const station = addRow(widget);
  station.addSpacer();
  addTram(station, 10);
  addLabel(station, board.from, Font.semiboldSystemFont(10), LOCK_FG);
  station.addSpacer();
  const time = widget.addStack();
  time.addSpacer();
  addClock(time, trips[0].departs, 14, 9, LOCK_FG);
  time.addSpacer();
  if (board.to) addLabel(widget, `to ${board.to}`, Font.systemFont(9), LOCK_DIM).centerAlignText();
  if (last && trips.length === 1) {
    const flag = widget.addStack();
    flag.addSpacer();
    addLastTrainFlag(flag, 9, LOCK_FG);
    flag.addSpacer();
  }
}

function buildInline(widget, loadedBoards) {
  widget.addImage(SFSymbol.named(TRAM_SYMBOL).image);
  widget.addText(
    loadedBoards
      .map(
        (loaded) =>
          `${loaded.board.from}\u2192 ${loaded.trips?.[0] ? clockFormat.string(loaded.trips[0].departs) : NO_DEPARTURE}`,
      )
      .join("  "),
  );
}

/**
 * @param {string} family  config.widgetFamily value
 */
function buildWidget(family, loadedBoards) {
  const widget = new ListWidget();
  widget.refreshAfterDate = new Date(Date.now() + REFRESH_MS);
  if (family === "accessoryInline") {
    buildInline(widget, loadedBoards);
    return widget;
  }
  if (family === "accessoryCircular") {
    buildCircular(widget, loadedBoards);
    return widget;
  }
  const root = widget.addStack();
  root.layoutVertically();
  if (family === "accessoryRectangular") {
    // the default padding is undocumented and shrinks the text, and the Lock Screen insets the widget itself
    widget.setPadding(0, 0, 0, 0);
    buildRectangular(root, loadedBoards);
    return widget;
  }
  widget.backgroundColor = BG;
  if (family === "small") {
    widget.setPadding(7, 12, 7, 12);
    buildSmall(root, loadedBoards);
  } else {
    widget.setPadding(10, 14, 10, 14);
    if (LIST_FAMILIES.includes(family)) buildStacked(root, loadedBoards);
    else buildColumns(root, loadedBoards);
  }
  return widget;
}

function buildErrorWidget(message) {
  const widget = new ListWidget();
  widget.backgroundColor = BG;
  widget.refreshAfterDate = new Date(Date.now() + REFRESH_MS);
  addLabel(widget, message, Font.systemFont(12), config.runsInAccessoryWidget ? LOCK_FG : FG);
  return widget;
}

function spokenDeparture({ minutes, scheduled }) {
  if (minutes === 0) return scheduled ? "scheduled to leave now" : "leaving now";
  const inMinutes = `in ${minutes} minute${minutes === 1 ? "" : "s"}`;
  return scheduled ? `scheduled ${inMinutes}` : inMinutes;
}

function spokenBoard(loaded) {
  const { spoken } = loaded.board;
  if (!loaded.trips) return `No data, ${spoken}.`;
  const [next, ...later] = loaded.trips;
  if (!next) return `No trains, ${spoken}.`;
  const transfer = next.transfer ? `, transfer at ${STATIONS[next.transfer.station].name}` : "";
  let rest = "";
  if (later.length > 0) rest = `, next ${later.map((t) => t.minutes).join(" and ")}`;
  else if (loaded.last) rest = `, ${LAST_TRAIN}`;
  return `${spoken} ${spokenDeparture(next)}${transfer}${rest}.`;
}

async function main() {
  const raw = String(args.widgetParameter ?? args.shortcutParameter ?? args.queryParameters.stations ?? "").trim();
  const custom = parseStations(raw);
  const codes = custom.length > 0 ? custom : parseStations(DEFAULT_STATIONS);
  const shown = config.runsInWidget && LIST_FAMILIES.includes(config.widgetFamily) ? TRAINS_PER_LIST : TRAINS_PER_BOARD;
  const loadedBoards = await loadBoards(boardsFor(codes, shown));

  if (config.runsInWidget) {
    Script.setWidget(buildWidget(config.widgetFamily, loadedBoards));
    return;
  }
  const text = loadedBoards.map(spokenBoard).join(" ");
  Script.setShortcutOutput(text);
  if (config.runsWithSiri) Speech.speak(text);
  if (config.runsInApp) await buildWidget("medium", loadedBoards).presentMedium();
}

/** Runs main(), showing an error in place of the widget when running in one. */
async function run() {
  deadline = Date.now() + RUN_DEADLINE_MS;
  try {
    await main();
  } catch (e) {
    if (!config.runsInWidget) throw e;
    Script.setWidget(buildErrorWidget(e.message));
  }
}

module.exports = { api: ENGINE_API, run };
