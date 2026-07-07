import { defineConfig } from 'vite';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, relative, sep } from 'node:path';

function contentTypeFor(path: string) {
	switch (extname(path).toLowerCase()) {
		case '.geojson':
		case '.json':
			return 'application/geo+json; charset=utf-8';
		case '.parquet':
		case '.geoparquet':
		case '.parq':
			return 'application/vnd.apache.parquet';
		default:
			return 'application/octet-stream';
	}
}

function localDataPlugin() {
	return {
		name: 'geovizwiz-local-data',
		configureServer(server: any) {
			const dataRoot = normalize(join(server.config.root, '..', 'local-data'));
			server.middlewares.use('/local-data', (req: any, res: any, next: any) => {
				const requestPath = decodeURIComponent((req.url || '').split('?')[0]).replace(/^\/+/, '');
				const target = normalize(join(dataRoot, requestPath));
				const rel = relative(dataRoot, target);
				if (rel.startsWith('..') || rel === '..' || rel.split(sep).includes('..')) {
					res.statusCode = 403;
					res.end('Forbidden');
					return;
				}
				if (!existsSync(target) || !statSync(target).isFile()) {
					next();
					return;
				}
				res.setHeader('Content-Type', contentTypeFor(target));
				createReadStream(target).pipe(res);
			});
		}
	};
}

export default defineConfig(({ mode }) => {
	const isDesktopMode = mode === 'desktop';
	return {
		// Browser/hosted deploys are served under /viz/ on GitHub Pages.
		// Desktop Electron file:// loads must use relative asset URLs.
		base: isDesktopMode ? './' : '/viz/',
		plugins: [localDataPlugin()]
	};
});
