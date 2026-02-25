import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';
import path from 'path';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	resolve: {
		alias: {
			'$config': path.resolve(import.meta.dirname, '../config'),
		},
	},
	build: {
		sourcemap: 'hidden',
		chunkSizeWarningLimit: 1200,
		rollupOptions: {
			output: {
				manualChunks(id) {
					if (id.includes('node_modules')) {
						if (id.includes('lightweight-charts') || id.includes('fancy-canvas')) return 'vendor-lwc';
						if (id.includes('echarts') || id.includes('zrender')) return 'vendor-echarts';
						if (id.includes('lucide')) return 'vendor-icons';
					}
				}
			}
		}
	}
});
