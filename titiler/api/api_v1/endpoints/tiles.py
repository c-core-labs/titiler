"""API tiles."""

from typing import Any, Dict, Optional

import os
from io import BytesIO
from urllib.parse import urlencode

import numpy

from fastapi import APIRouter, Depends, Query, Path
from starlette.requests import Request
from starlette.responses import Response

from rasterio.transform import from_bounds
from rio_tiler_crs import COGReader
from rio_tiler.utils import render
from rio_tiler.profiles import img_profiles

from titiler.api import utils
from titiler.api.deps import CommonImageParams, TileMatrixSetNames, morecantile
from titiler.db.memcache import CacheLayer
from titiler.ressources.enums import ImageType
from titiler.ressources.common import drivers, mimetype
from titiler.ressources.responses import TileResponse
from titiler.core import config
from titiler.models.mapbox import TileJSON


router = APIRouter()

params: Dict[str, Any] = {
    "responses": {
        200: {
            "content": {
                "image/png": {},
                "image/jpg": {},
                "image/webp": {},
                "image/tiff": {},
                "application/x-binary": {},
            },
            "description": "Return an image.",
        }
    },
    "response_class": TileResponse,
}


@router.get(r"/cogs/{z}/{x}/{y}", **params)
@router.get(r"/cogs/{z}/{x}/{y}\.{ext}", **params)
@router.get(r"/cogs/{z}/{x}/{y}@{scale}x", **params)
@router.get(r"/cogs/{z}/{x}/{y}@{scale}x\.{ext}", **params)
@router.get(r"/cogs/{identifier}/{z}/{x}/{y}", **params)
@router.get(r"/cogs/{identifier}/{z}/{x}/{y}\.{ext}", **params)
@router.get(r"/cogs/{identifier}/{z}/{x}/{y}@{scale}x", **params)
@router.get(r"/cogs/{identifier}/{z}/{x}/{y}@{scale}x\.{ext}", **params)
async def tile(
    z: int = Path(..., ge=0, le=30, description="Mercator tiles's zoom level"),
    x: int = Path(..., description="Mercator tiles's column"),
    y: int = Path(..., description="Mercator tiles's row"),
    identifier: TileMatrixSetNames = Query(
        TileMatrixSetNames.WebMercatorQuad,  # type: ignore
        description="TileMatrixSet Name (default: 'WebMercatorQuad')",
    ),
    scale: int = Query(
        1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
    ),
    ext: ImageType = Query(None, description="Output image type. Default is auto."),
    url: str = Query(..., description="Cloud Optimized GeoTIFF URL."),
    image_params: CommonImageParams = Depends(CommonImageParams),
    cache_client: CacheLayer = Depends(utils.get_cache),
) -> TileResponse:
    """Handle /tiles requests."""
    timings = []
    headers: Dict[str, str] = {}

    tile_hash = utils.get_hash(
        **dict(
            identifier=identifier.name,
            z=z,
            x=x,
            y=y,
            ext=ext,
            scale=scale,
            url=url,
            indexes=image_params.indexes,
            nodata=image_params.nodata,
            rescale=image_params.rescale,
            color_formula=image_params.color_formula,
            color_map=image_params.color_map,
        )
    )
    tilesize = scale * 256
    tms = morecantile.tms.get(identifier.name)

    content = None
    if cache_client:
        try:
            content, ext = cache_client.get_image_from_cache(tile_hash)
            headers["X-Cache"] = "HIT"
        except Exception:
            content = None

    if not content:
        with utils.Timer() as t:
            with COGReader(url, tms=tms) as cog:
                tile, mask = cog.tile(
                    x,
                    y,
                    z,
                    tilesize=tilesize,
                    indexes=image_params.indexes,
                    expression=image_params.expression,
                    nodata=image_params.nodata,
                )
                colormap = image_params.color_map or cog.colormap

        timings.append(("Read", t.elapsed))

        if not ext:
            ext = ImageType.jpg if mask.all() else ImageType.png

        with utils.Timer() as t:
            tile = utils.postprocess(
                tile,
                mask,
                rescale=image_params.rescale,
                color_formula=image_params.color_formula,
            )
        timings.append(("Post-process", t.elapsed))

        with utils.Timer() as t:
            if ext == ImageType.npy:
                sio = BytesIO()
                numpy.save(sio, (tile, mask))
                sio.seek(0)
                content = sio.getvalue()
            else:
                driver = drivers[ext.value]
                options = img_profiles.get(driver.lower(), {})
                if ext == ImageType.tif:
                    bounds = tms.xy_bounds(x, y, z)
                    dst_transform = from_bounds(*bounds, tilesize, tilesize)
                    options = {"crs": tms.crs, "transform": dst_transform}
                content = render(
                    tile, mask, img_format=driver, colormap=colormap, **options
                )
        timings.append(("Format", t.elapsed))

        if cache_client and content:
            cache_client.set_image_cache(tile_hash, (content, ext))

    if timings:
        headers["X-Server-Timings"] = "; ".join(
            ["{} - {:0.2f}".format(name, time * 1000) for (name, time) in timings]
        )

    return TileResponse(content, media_type=mimetype[ext.value], headers=headers)


tilejson_include = {
    "tilejson",
    "scheme",
    "version",
    "minzoom",
    "maxzoom",
    "bounds",
    "center",
    "tiles",
}


@router.get(
    "/cogs/tilejson.json",
    response_model=TileJSON,
    responses={200: {"description": "Return a tilejson"}},
    response_model_include=tilejson_include,  # https://github.com/tiangolo/fastapi/issues/528#issuecomment-589659378
)
@router.get(
    "/cogs/{identifier}/tilejson.json",
    response_model=TileJSON,
    responses={200: {"description": "Return a tilejson"}},
    response_model_include=tilejson_include,  # https://github.com/tiangolo/fastapi/issues/528#issuecomment-589659378
)
async def tilejson(
    request: Request,
    response: Response,
    identifier: TileMatrixSetNames = Query(
        TileMatrixSetNames.WebMercatorQuad,  # type: ignore
        description="TileMatrixSet Name (default: 'WebMercatorQuad')",
    ),
    url: str = Query(..., description="Cloud Optimized GeoTIFF URL."),
    tile_format: Optional[ImageType] = Query(
        None, description="Output image type. Default is auto."
    ),
    tile_scale: int = Query(
        1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
    ),
):
    """Handle /tilejson.json requests."""
    scheme = request.url.scheme
    host = request.headers["host"]
    if config.API_VERSION_STR:
        host += config.API_VERSION_STR

    kwargs = dict(request.query_params)
    kwargs.pop("tile_format", None)
    kwargs.pop("tile_scale", None)
    kwargs.pop("identifier", None)

    qs = urlencode(list(kwargs.items()))
    if tile_format:
        tile_url = f"{scheme}://{host}/cogs/{identifier.name}/{{z}}/{{x}}/{{y}}@{tile_scale}x.{tile_format}?{qs}"
    else:
        tile_url = f"{scheme}://{host}/cogs/{identifier.name}/{{z}}/{{x}}/{{y}}@{tile_scale}x?{qs}"

    tms = morecantile.tms.get(identifier.name)
    with COGReader(url, tms=tms) as cog:
        tjson = {
            "bounds": cog.bounds,
            "center": cog.center,
            "minzoom": cog.minzoom,
            "maxzoom": cog.maxzoom,
            "name": os.path.basename(url),
            "tiles": [tile_url],
        }

    response.headers["Cache-Control"] = "max-age=3600"
    return tjson
