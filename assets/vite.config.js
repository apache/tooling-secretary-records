import path from "path"
import { defineConfig } from "vite"
import { viteStaticCopy } from 'vite-plugin-static-copy'
import { svelte } from '@sveltejs/vite-plugin-svelte'

const outRootDir = path.join(import.meta.dirname, "../app/client/static", "assets")
const outVendorDir = "vendor"

export default defineConfig(({ mode }) => {
    const isDev = mode === 'development'

    return {
        root: path.join(import.meta.dirname, "./src/"),
        base: "/assets",

        plugins: [
            svelte({
                configFile: '../svelte.config.js'
            }),
            viteStaticCopy({
                targets: [
                    // bootstrap
                    {
                        src: "../node_modules/bootstrap/dist/js/bootstrap.bundle.min.js",
                        dest: outVendorDir + "/js"
                    },
                    {
                        src: "../node_modules/bootstrap/dist/js/bootstrap.bundle.min.js.map",
                        dest: outVendorDir + "/js"
                    },
                    {
                        src: "../node_modules/bootstrap/dist/css/bootstrap.min.css",
                        dest: outVendorDir + "/css"
                    },
                    {
                        src: "../node_modules/bootstrap/dist/css/bootstrap.min.css.map",
                        dest: outVendorDir + "/css"
                    },
                    // bootstrap-icons
                    {
                        src: "../node_modules/bootstrap-icons/font/bootstrap-icons.min.css",
                        dest: outVendorDir + "/css"
                    },
                    {
                        src: "../node_modules/bootstrap-icons/font/fonts/*",
                        dest: outVendorDir + "/css/fonts"
                    }
                ]
            })
        ],
        resolve: {
            alias: {
                '@': path.resolve('./src'),
            }
        },
        build: {
            minify: !isDev,
            manifest: "manifest.json",
            assetsDir: "bundled",
            outDir: outRootDir,
            sourcemap: true,
            emptyOutDir: true,
            rollupOptions: {
                preserveEntrySignatures: 'exports-only',
                input: [
                    "src/js/pages/home.ts",
                    "src/css/app.css",
                ],
                output: {
                    entryFileNames: 'bundled/[name]-[hash].js',
                    assetFileNames: 'bundled/[name]-[hash][extname]',
                    chunkFileNames: 'bundled/chunk-[name]-[hash].js'
                }
            }
        }
    }
})
