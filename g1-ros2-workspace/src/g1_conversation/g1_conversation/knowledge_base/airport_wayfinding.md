# Airport wayfinding and mapped destinations

## Purpose
Help passengers find places within the airport using verified airport information and the active map. This knowledge base is generic prototype guidance, not a surveyed airport layout.

## Common destinations
Passengers may ask for boarding gates, check-in counters, baggage claim, washrooms/toilets, accessible washrooms, information desks, elevators/lifts, prayer rooms, medical assistance, food outlets or exits. Ask which terminal, floor or gate they mean if needed.

## Directions and map labels
Give exact directions only when a verified route is supplied for the current airport, terminal and floor. Do not invent a left/right turn, corridor, landmark, gate number, distance or coordinates. A destination name alone does not establish a route.

Saved destinations are maintained in the navigation console's Maps & Localization tab. Each active map can have a `<map-name>_labels.json` file. Labels from another map or floor must not be reused as though their positions matched. The conversation RAG agent does not automatically read these JSON files or send Nav2 goals.

## When a route is unavailable
Say that the exact location is not verified. Ask for the terminal/floor if useful, then direct the passenger to airport signs or the information desk for confirmed directions. Example: "I can help you find a washroom. Which terminal or floor are you in?"

## Route accessibility
Ask whether a step-free route is needed. Only recommend an elevator or accessible route when verified. Do not direct a passenger through staff-only areas, across vehicle lanes or through a security checkpoint without the required authorization.

## Navigation handoff
Physical navigation requires the correct map, a localized robot pose, a saved/validated destination and a ready navigation system. Navigation is controlled separately in the console. Never announce that the robot is moving or has arrived based only on a spoken response.
