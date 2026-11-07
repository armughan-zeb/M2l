# Local map data sources

- World country outlines: Natural Earth 1:110m, bundled locally in `ne_110m_admin_0_countries.json`.
- Detailed country and admin-1 boundaries for India and Japan: Natural Earth 1:10m, prepared from the official Natural Earth cultural-vector downloads. The local subset contains both country polygons and the India state/Japan prefecture polygons needed for regional spotlights. Natural Earth data is public domain.
- Osaka city point: GeoNames place record for Osaka, Japan (`34.69379, 135.50107`). This is used because the bundled 1:110m Natural Earth populated-place file omits Osaka.
- Cape Canaveral regional locator: NASA's Kennedy Space Center image catalog (`28.5, -80.63`). This is a regional reference, not an individual launch-pad coordinate. Source: https://visibleearth.nasa.gov/images/52455/kennedy-space-center-florida
- Shaded terrain base: NASA Earth Observatory, Blue Marble: Next Generation with Topography, January 2004, global 2 km JPEG. The packaged image was reprojected to Web Mercator locally to match the vector map layer. Credit: NASA Earth Observatory.

Downloads and attribution:

- Natural Earth 10m cultural vectors: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/
- Natural Earth 1:110m cultural vectors: https://www.naturalearthdata.com/downloads/110m-cultural-vectors/
- NASA Blue Marble: Next Generation with Topography: https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/base-topography/
- GeoNames Osaka record: https://www.geonames.org/1853909/osaka.html

The renderer reads only these packaged map assets. Final renders do not request map tiles or geographic features over the network.
