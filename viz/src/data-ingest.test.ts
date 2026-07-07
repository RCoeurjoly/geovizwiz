import { describe, expect, it } from 'vitest';
import { inferGeoJSONFields, isGeoJsonFileName, parseGeoJSONFeatureCollection } from './data-ingest';

describe('data ingest helpers', () => {
  it('recognizes GeoJSON file names', () => {
    expect(isGeoJsonFileName('oslo-no-parcels.geojson')).toBe(true);
    expect(isGeoJsonFileName('oslo.geo.json')).toBe(true);
    expect(isGeoJsonFileName('project.json')).toBe(true);
    expect(isGeoJsonFileName('oslo.parquet')).toBe(false);
  });

  it('parses a GeoJSON FeatureCollection directly or from a wrapper', () => {
    const fc = { type: 'FeatureCollection', features: [] };

    expect(parseGeoJSONFeatureCollection(JSON.stringify(fc)).features).toHaveLength(0);
    expect(parseGeoJSONFeatureCollection(JSON.stringify({ geojson: fc })).features).toHaveLength(0);
    expect(() => parseGeoJSONFeatureCollection(JSON.stringify({ type: 'Feature' }))).toThrow(/FeatureCollection/);
  });

  it('infers numeric and categorical fields from GeoJSON properties', () => {
    const fc: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { parcel_id: '0301-1/1', REALLANDVA: 1000, tax_basis_nok: '900', address: 'Akersgata 1' },
          geometry: { type: 'Point', coordinates: [10.75, 59.91] },
        },
        {
          type: 'Feature',
          properties: { parcel_id: '0301-1/2', REALLANDVA: 2000, tax_basis_nok: '1800', address: 'Akersgata 2' },
          geometry: { type: 'Point', coordinates: [10.76, 59.92] },
        },
      ],
    };

    expect(inferGeoJSONFields(fc)).toEqual({
      numeric: ['REALLANDVA', 'tax_basis_nok'],
      categorical: ['address', 'parcel_id'],
    });
  });
});
