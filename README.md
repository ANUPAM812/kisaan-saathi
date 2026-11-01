# kisaan-saathi
Live weather + soil advisory for Indian farmers, in simple Hinglish.

## 1. What the project does

Farmers often don't know when heavy rain is coming, when a dry spell may start,
when to sow, when to irrigate, or how much damage the weather could do to their crop.

Kisan Saathi asks the farmer for:
1. their village / town,
2. four easy questions about their soil (how it behaves when rolled, colour, drainage, cracks),
3. their crop, growth stage and whether they have irrigation.

It then returns, in simple language:
- heavy-rain alerts (IMD threshold: 64.5 mm/day) and drought / dry-spell risk
- best days to sow in the next 10 days
- an irrigation schedule (in mm of water)
- a 0-100 crop damage risk score (waterlogging, drought, heat/cold stress)
- the soil type (Black, Red, Alluvial or Sandy), with clay / sand / silt and pH

## 2. Live data (no dummy data)

All weather and soil values come from live public APIs. Nothing is hardcoded or mocked.

| Source | Used for |
|---|---|
| Open-Meteo Geocoding API | Converts place name to latitude / longitude |
| Open-Meteo Forecast API | 30 past days + 16 forecast days of rain, temperature and evapotranspiration (ET0) |
| ISRIC SoilGrids REST API | Real clay, sand, pH, organic carbon and CEC at the farm location |

If SoilGrids is unavailable, pH / CEC / carbon are shown as "not available".
The app never invents values.

## 3. Architecture

    Browser (index.html)
          |
          |  GET /api/advice?place=...&crop=...
          v
    Flask backend (kisan_saathi.py)
          |-- Open-Meteo Geocoding  -> latitude, longitude
          |-- Open-Meteo Forecast   -> daily rain, temperature, ET0
          |-- ISRIC SoilGrids       -> clay, sand, pH, carbon, CEC
          v
    Soil model -> Water-balance simulation -> Risk engine -> Advice (JSON)

Tech stack: Python, Flask, requests, gunicorn,
