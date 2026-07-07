export function isGeoJsonFileName(name: string): boolean {
  const lowerName = name.toLowerCase();
  return lowerName.endsWith('.geojson') || lowerName.endsWith('.json') || lowerName.endsWith('.geo.json');
}

export function parseGeoJSONFeatureCollection(text: string): GeoJSON.FeatureCollection {
  const parsed = JSON.parse(text);
  const fc: GeoJSON.FeatureCollection | undefined =
    parsed?.type === 'FeatureCollection' ? parsed : parsed?.geojson;
  if (!fc?.features) throw new Error('Parser returned no FeatureCollection.');
  return fc;
}

export function inferGeoJSONFields(fc: GeoJSON.FeatureCollection): { numeric: string[]; categorical: string[] } {
  const samples = fc.features.slice(0, 5000);
  const profiles = new Map<string, { seen: number; numeric: number }>();

  for (const feature of samples) {
    const props = feature.properties ?? {};
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === '') continue;
      const profile = profiles.get(key) ?? { seen: 0, numeric: 0 };
      profile.seen += 1;
      if (typeof value === 'number' && Number.isFinite(value)) {
        profile.numeric += 1;
      } else if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) {
        profile.numeric += 1;
      }
      profiles.set(key, profile);
    }
  }

  const numeric: string[] = [];
  const categorical: string[] = [];
  for (const [field, profile] of profiles) {
    if (profile.seen > 0 && profile.numeric === profile.seen) numeric.push(field);
    else categorical.push(field);
  }
  return { numeric: numeric.sort(), categorical: categorical.sort() };
}
