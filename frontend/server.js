// Custom standalone server that serves static files from /public alongside Next.js
const http = require('http');
const path = require('path');
const fs = require('fs');

const dir = path.join(__dirname);
const publicDir = path.join(dir, 'public');

process.env.NODE_ENV = 'production';
process.chdir(__dirname);

const currentPort = parseInt(process.env.PORT, 10) || 3000;
const hostname = process.env.HOSTNAME || '0.0.0.0';

let keepAliveTimeout = parseInt(process.env.KEEP_ALIVE_TIMEOUT, 10);
if (
  Number.isNaN(keepAliveTimeout) ||
  !Number.isFinite(keepAliveTimeout) ||
  keepAliveTimeout < 0
) {
  keepAliveTimeout = undefined;
}

// Load Next.js config
const nextConfigPath = path.join(dir, 'package.json');
const { nextConfig = {} } = require(nextConfigPath);
process.env.__NEXT_PRIVATE_STANDALONE_CONFIG = JSON.stringify(nextConfig);

const { createServer } = require('next');
const nextApp = createServer({
  dev: false,
  dir,
  quiet: false,
  conf: nextConfig,
});

nextApp.prepare().then(() => {
  const server = http.createServer((req, res) => {
    const url = req.url || '/';

    // Check if request is for a static file in /public
    if (url.startsWith('/favicon') || url.startsWith('/robots') || url.startsWith('/sitemap')) {
      const staticFilePath = path.join(publicDir, url);

      // Security: prevent directory traversal
      if (!staticFilePath.startsWith(publicDir)) {
        res.writeHead(403);
        res.end('Forbidden');
        return;
      }

      fs.readFile(staticFilePath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end('Not Found');
          return;
        }

        const ext = path.extname(url);
        const contentTypes = {
          '.ico': 'image/x-icon',
          '.png': 'image/png',
          '.jpg': 'image/jpeg',
          '.jpeg': 'image/jpeg',
          '.gif': 'image/gif',
          '.svg': 'image/svg+xml',
          '.webp': 'image/webp',
          '.txt': 'text/plain',
          '.xml': 'application/xml',
          '.json': 'application/json',
        };

        const contentType = contentTypes[ext] || 'application/octet-stream';
        res.writeHead(200, {
          'Content-Type': contentType,
          'Cache-Control': 'public, max-age=31536000, immutable',
        });
        res.end(data);
      });
      return;
    }

    // Let Next.js handle all other requests
    nextApp.getRequestHandler()(req, res, url);
  });

  server.listen(currentPort, hostname, () => {
    console.log(`> Ready on http://${hostname}:${currentPort}`);
  });

  server.keepAliveTimeout = keepAliveTimeout || 60000;
});
