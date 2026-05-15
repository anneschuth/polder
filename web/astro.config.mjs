import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://anneschuth.nl',
  base: '/polder',
  trailingSlash: 'always',
  build: {
    format: 'directory',
  },
  vite: {
    ssr: {
      noExternal: ['@nldd/design-system'],
    },
  },
});
