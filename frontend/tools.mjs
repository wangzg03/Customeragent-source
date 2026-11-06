import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { build, createServer, preview } from 'vite'
import vue from '@vitejs/plugin-vue'

const root = dirname(fileURLToPath(import.meta.url))
const command = process.argv[2] ?? 'build'
const options = { root, configFile: false, plugins: [vue()] }

function sources(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sources(path) : /\.(ts|vue)$/.test(path) ? [path] : []
  })
}

function check() {
  const result = spawnSync(process.execPath, [
    join(root, 'node_modules/vue-tsc/bin/vue-tsc.js'),
    '--noEmit', '--target', 'ES2020', '--module', 'ESNext',
    '--moduleResolution', 'bundler', '--lib', 'ES2020,DOM,DOM.Iterable',
    '--strict', '--skipLibCheck', '--useDefineForClassFields',
    '--allowImportingTsExtensions', '--resolveJsonModule', '--isolatedModules',
    '--jsx', 'preserve', '--noUnusedLocals', '--noUnusedParameters',
    '--noFallthroughCasesInSwitch', ...sources(join(root, 'src')),
  ], { cwd: root, stdio: 'inherit' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

if (command === 'check') {
  check()
} else if (command === 'build') {
  check()
  await build(options)
} else if (command === 'dev') {
  const server = await createServer({ ...options, server: {
    host: '127.0.0.1',
    proxy: { '/api': 'http://127.0.0.1:8001' },
  } })
  await server.listen()
  server.printUrls()
} else if (command === 'preview') {
  const server = await preview({ ...options, preview: { host: '127.0.0.1' } })
  server.printUrls()
} else {
  throw new Error('Usage: node tools.mjs dev|build|preview|check')
}
