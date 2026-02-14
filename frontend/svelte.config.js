import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
    kit: {
        // adapter-static is perfect for Firebase Hosting as an SPA
        adapter: adapter({
            pages: 'build',
            assets: 'build',
            fallback: 'index.html', // This is the "magic" line for SPAs
            precompress: false,
            strict: true
        })
    }
};

export default config;