# Description

Search and browse anime from MyAnimeList using the Jikan API — find shows by title, genre, season, or popularity with ratings, synopses, and full details.

# Anime Search (jikan-anime)

This skill teaches you how to use the tools in the anime toolbox effectively.

## Available Tools

- `search_anime(query, type, status, rating, genre, order_by, sort, page, limit)` — Search for anime by title with optional filters.
- `get_anime(mal_id)` — Get full details for a specific anime by its MyAnimeList ID.
- `list_genres()` — List all valid anime genres that can be used as filters.
- `get_top_anime(type, filter, page, limit)` — Get the top-ranked anime on MyAnimeList.
- `get_seasonal_anime(year, season, page, limit)` — Get anime from a specific season.

## Common Patterns

### Finding an anime by name
```
search_anime(query="Attack on Titan")
```

### Getting details about a specific anime
```
get_anime(mal_id=16498)
```

### Top action anime
```
search_anime(query="", genre="Action", order_by="score", limit=5)
```

### Summer 2026 seasonal anime
```
get_seasonal_anime(year=2026, season="summer", limit=5)
```
