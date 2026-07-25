"""Search for anime using the Jikan (MyAnimeList) API.

Public tool functions:
    search_anime(query, type, status, rating, genre, order_by, sort, page, limit)
    get_anime(mal_id)
    list_genres()
    get_top_anime(type, filter, page, limit)
    get_seasonal_anime(year, season, page, limit)
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

BASE_URL = "https://api.jikan.moe/v4"


async def search_anime(
    query: str,
    type: str = "",
    status: str = "",
    rating: str = "",
    genre: str = "",
    order_by: str = "",
    sort: str = "desc",
    page: int = 1,
    limit: int = 5,
) -> dict[str, Any]:
    """Search for anime by title, with optional filters.

    Uses the Jikan v4 API to query MyAnimeList. Returns ranked results
    with titles, synopses, scores, genres, episodes, and more.

    Args:
        query: Search term (e.g. "Attack on Titan", "isekai", "Ghibli").
        type: Filter by type: "tv", "movie", "ova", "special", "ona", "music".
            Empty string for all types.
        status: Filter by airing status: "airing", "complete", "upcoming".
            Empty string for all.
        rating: Filter by age rating: "g", "pg", "pg13", "r17", "r", "rx".
            Empty string for all.
        genre: Filter by genre name (e.g. "Action", "Comedy", "Romance").
            Call list_genres() first to see valid genre names.
        order_by: Sort by: "title", "score", "popularity", "rank",
            "start_date", "episodes", "favorites". Empty for relevance.
        sort: Sort direction: "desc" (default) or "asc".
        page: Page number (starting at 1).
        limit: Results per page (1-25, default 5).

    Returns:
        A dict with "results" list and pagination info, or an error message.
    """
    params: dict[str, Any] = {
        "q": query.strip(),
        "page": page,
        "limit": max(1, min(limit, 25)),
    }
    if order_by:
        params["order_by"] = order_by
    if sort:
        params["sort"] = sort
    if type:
        params["type"] = type
    if status:
        params["status"] = status
    if rating:
        params["rating"] = rating
    if genre:
        genre_id = await _resolve_genre_id(genre)
        if genre_id is None:
            return {
                "error": (
                    f"Unknown genre '{genre}'. Call list_genres() to see "
                    "valid genre names."
                )
            }
        params["genres"] = str(genre_id)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{BASE_URL}/anime", params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        return {"error": f"Jikan API error {exc.response.status_code}: {exc}"}
    except Exception as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    results: list[dict[str, Any]] = []
    for item in data.get("data", []):
        genres_list = [g["name"] for g in item.get("genres", []) if isinstance(g, dict)]
        results.append(
            {
                "mal_id": item.get("mal_id"),
                "title": item.get("title", ""),
                "title_english": item.get("title_english"),
                "title_japanese": item.get("title_japanese"),
                "type": item.get("type", ""),
                "episodes": item.get("episodes"),
                "status": item.get("status", ""),
                "score": item.get("score"),
                "scored_by": item.get("scored_by"),
                "rank": item.get("rank"),
                "popularity": item.get("popularity"),
                "synopsis": _clean_synopsis(item.get("synopsis", "")),
                "genres": genres_list,
                "rating": item.get("rating", ""),
                "url": item.get("url", ""),
                "images": {
                    "small": (
                        item.get("images", {}).get("jpg", {}).get("small_image_url", "")
                    ),
                    "large": (
                        item.get("images", {}).get("jpg", {}).get("large_image_url", "")
                    ),
                },
            }
        )

    pagination = data.get("pagination", {})
    return {
        "results": results,
        "page": pagination.get("current_page", page),
        "has_next_page": pagination.get("has_next_page", False),
        "total_results": pagination.get("items", {}).get("total", 0),
    }


async def get_anime(mal_id: int) -> dict[str, Any]:
    """Get detailed information about a specific anime by MyAnimeList ID.

    Args:
        mal_id: The MyAnimeList anime ID (e.g. 16498 for Attack on Titan).

    Returns:
        A dict with full anime details, or an error message.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{BASE_URL}/anime/{mal_id}/full")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return {"error": f"Anime with ID {mal_id} not found."}
        return {"error": f"Jikan API error {exc.response.status_code}: {exc}"}
    except Exception as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    item = data.get("data", {})
    if not item:
        return {"error": f"No data found for anime ID {mal_id}."}

    genres_list = [g["name"] for g in item.get("genres", []) if isinstance(g, dict)]
    studios_list = [s["name"] for s in item.get("studios", []) if isinstance(s, dict)]
    producers_list = [
        p["name"] for p in item.get("producers", []) if isinstance(p, dict)
    ]

    return {
        "mal_id": item.get("mal_id"),
        "title": item.get("title", ""),
        "title_english": item.get("title_english"),
        "title_japanese": item.get("title_japanese"),
        "type": item.get("type", ""),
        "source": item.get("source", ""),
        "episodes": item.get("episodes"),
        "status": item.get("status", ""),
        "duration": item.get("duration", ""),
        "rating": item.get("rating", ""),
        "score": item.get("score"),
        "scored_by": item.get("scored_by"),
        "rank": item.get("rank"),
        "popularity": item.get("popularity"),
        "favorites": item.get("favorites"),
        "synopsis": _clean_synopsis(item.get("synopsis", "")),
        "background": item.get("background"),
        "season": item.get("season"),
        "year": item.get("year"),
        "genres": genres_list,
        "studios": studios_list,
        "producers": producers_list,
        "url": item.get("url", ""),
        "images": {
            "small": (item.get("images", {}).get("jpg", {}).get("small_image_url", "")),
            "large": (item.get("images", {}).get("jpg", {}).get("large_image_url", "")),
        },
    }


async def list_genres() -> dict[str, Any]:
    """List all valid anime genres that can be used as filters.

    Returns:
        A dict with a "genres" list of {mal_id, name, count} entries.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{BASE_URL}/genres/anime")
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    genres = [
        {
            "mal_id": g.get("mal_id"),
            "name": g.get("name"),
            "count": g.get("count"),
        }
        for g in data.get("data", [])
    ]
    return {"genres": genres}


async def get_top_anime(
    type: str = "",
    filter: str = "",
    page: int = 1,
    limit: int = 5,
) -> dict[str, Any]:
    """Get the top-ranked anime on MyAnimeList.

    Args:
        type: Filter by type: "tv", "movie", "ova", "special", "ona", "music".
            Empty string for all types.
        filter: Filter by: "airing", "upcoming", "bypopularity", "favorite".
            Empty string for default ranking.
        page: Page number (starting at 1).
        limit: Results per page (1-25, default 5).

    Returns:
        A dict with "results" list and pagination info.
    """
    params: dict[str, Any] = {
        "page": page,
        "limit": max(1, min(limit, 25)),
    }
    if type:
        params["type"] = type
    if filter:
        params["filter"] = filter

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{BASE_URL}/top/anime", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    results: list[dict[str, Any]] = []
    for item in data.get("data", []):
        genres_list = [g["name"] for g in item.get("genres", []) if isinstance(g, dict)]
        results.append(
            {
                "mal_id": item.get("mal_id"),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "episodes": item.get("episodes"),
                "score": item.get("score"),
                "rank": item.get("rank"),
                "genres": genres_list,
                "synopsis": _clean_synopsis(item.get("synopsis", "")),
                "url": item.get("url", ""),
                "images": {
                    "small": (
                        item.get("images", {}).get("jpg", {}).get("small_image_url", "")
                    ),
                },
            }
        )

    pagination = data.get("pagination", {})
    return {
        "results": results,
        "page": pagination.get("current_page", page),
        "has_next_page": pagination.get("has_next_page", False),
        "total_results": pagination.get("items", {}).get("total", 0),
    }


async def get_seasonal_anime(
    year: int = 0,
    season: str = "",
    page: int = 1,
    limit: int = 5,
) -> dict[str, Any]:
    """Get anime from a specific season (e.g. Winter 2026, Spring 2025).

    Args:
        year: The year. Defaults to current year if 0.
        season: Season name: "winter", "spring", "summer", "fall".
            Defaults to current season if empty.
        page: Page number (starting at 1).
        limit: Results per page (1-25, default 5).

    Returns:
        A dict with "results" list and pagination info.
    """
    params: dict[str, Any] = {
        "page": page,
        "limit": max(1, min(limit, 25)),
    }
    path = f"{BASE_URL}/seasons"
    if year and season:
        path += f"/{year}/{season}"
    elif year:
        path += f"/{year}"
    else:
        path += "/now"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return {"error": f"Request failed: {type(exc).__name__}: {exc}"}

    results: list[dict[str, Any]] = []
    for item in data.get("data", []):
        genres_list = [g["name"] for g in item.get("genres", []) if isinstance(g, dict)]
        results.append(
            {
                "mal_id": item.get("mal_id"),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "episodes": item.get("episodes"),
                "score": item.get("score"),
                "genres": genres_list,
                "synopsis": _clean_synopsis(item.get("synopsis", "")),
                "url": item.get("url", ""),
                "images": {
                    "small": (
                        item.get("images", {}).get("jpg", {}).get("small_image_url", "")
                    ),
                },
            }
        )

    pagination = data.get("pagination", {})
    return {
        "results": results,
        "page": pagination.get("current_page", page),
        "has_next_page": pagination.get("has_next_page", False),
        "total_results": pagination.get("items", {}).get("total", 0),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_genre_cache: dict[str, int] | None = None


async def _fetch_genre_map() -> dict[str, int]:
    """Fetch all genres from Jikan and return a {name_lower: mal_id} mapping."""
    global _genre_cache
    if _genre_cache is not None:
        return _genre_cache
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{BASE_URL}/genres/anime")
            response.raise_for_status()
            data = response.json()
    except Exception:
        return {}
    mapping: dict[str, int] = {}
    for g in data.get("data", []):
        name = g.get("name", "")
        if name:
            mapping[name.lower()] = g.get("mal_id", 0)
    _genre_cache = mapping
    return mapping


async def _resolve_genre_id(name: str) -> int | None:
    """Resolve a genre name (case-insensitive) to its MAL ID."""
    genre_map = await _fetch_genre_map()
    return genre_map.get(name.strip().lower())


def _clean_synopsis(text: str | None) -> str:
    """Strip HTML/BBcode tags from a synopsis string."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[/?[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
