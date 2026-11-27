// @ts-check
import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// 部署至 Cloudflare Pages 或 Vercel 前，請將 site 改為您的正式網域
export default defineConfig({
  site: 'https://daily-price-calc.pages.dev',
  output: 'static',
  integrations: [sitemap()],
});
