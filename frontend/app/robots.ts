import { MetadataRoute } from 'next';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/api/', '/account/', '/admin/'],
      },
    ],
    sitemap: 'https://hanacv2sql.com/sitemap.xml',
  };
}
