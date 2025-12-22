# Helios

**Helios** is a utility for discovering, managing, and synchronizing game libraries with **Apollo**.

It allows you to:
- Add/remove games from Apollo
- Discover installed games from Steam, Epic, and Non-Steam app entries
- Automatically add library cover art to Apollo for added application(s)

---

## Features

### Library Discovery
- Steam (including Non-Steam shortcuts)
- Epic Games Launcher
- Apollo apps added via the web interface
  
### App Management
- Allows **quick addition** and **removal** of games in the above libraries
- **Interactive selection** or direct input of Helios created UUID (Universally Unique Identifier) to **add*8 or **remove** apps to/from **Apollo**
- **Filter** available **apps** by **source, type, name, or managed state**
- **Tracks** which **apps** are **managed by Helios** using a locally created JSON file (`%localappdata%/Helios/apps.json`)
- **Automatically creates** Apollo compatible **cover art files** using the set artwork in the respective game's library

### Validation
- **Verifies** all managed apps have **valid covers**
- **Restores** missing or corrupted images
- **Optional cleanup** of **orphaned covers**

### Status & Inspection
- High-level Helios health overview
- Library info for Steam and/or Epic Games Launcher

---

## Requirements

- **Windows**
- **Python 3.10+**
- **Apollo** (installed)
- **Steam** and/or **Epic Games Launcher**

### Python Dependencies

```
pip install pillow requests
```
---
# Usage
## Add Apps
```
#Retrieve list of installed library apps
helios --add

#Add by explicit Helios ID/UUID
helios --add <Helios ID (UUID)>

#Add using optional filters
helios --add --search <name> --source <library> --type <Application/Game/Tool>
```

## Remove Apps
```
#Retrieve list of Helios managed apps
helios --remove

#Remove by explicit Helios ID/UUID
helios --remove <Helios ID (UUID)>

#Remove using optional filters
helios --remove --search <name> --source <library> --type <Application/Game/Tool>
```

## Library Status/Info/
```
#Get library information or Helios status
helios --status <Epic/Steam> <--show-sample>
```

## Cache Management
```
#Rebuild entire Helios cache
helios --cache

#Rebuild specific library cache
helios --cache <steam/nonsteam/epic>
```

## Cover Art Maintenance
```
#Validate cover art and remove orphans
helios --cleanup-covers
```

## Example Output
```
Unmanaged apps matching your filters:
Option | Name                 | Source   | Type   | Helios ID (UUID)
----------------------------------------------------------------------------------------
1      | Half-Life            | Steam    | Game   | 782A4AB5-3C83-574B-9995-11AECF09D4D5
Enter numbers to add (comma-separated), 'all', or 'q': 1

Add results:
Name                 | Source   | Helios ID (UUID)                     | Status
-----------------------------------------------------------------------------------------------------------------
Half-Life            | Steam    | 782A4AB5-3C83-574B-9995-11AECF09D4D5 | Added to Vibepollo and managed by Helios
```
