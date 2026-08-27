/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { resolveOpenApiPath, resolvePowerContextRoot } from '../scripts/sync-openapi.mjs'

const originalEnv = {
  POWERCONTEXT_OPENAPI: process.env.POWERCONTEXT_OPENAPI,
  POWERCONTEXT_ROOT: process.env.POWERCONTEXT_ROOT,
}

function clearOverrides() {
  delete process.env.POWERCONTEXT_OPENAPI
  delete process.env.POWERCONTEXT_ROOT
}

afterEach(() => {
  if (originalEnv.POWERCONTEXT_OPENAPI === undefined) delete process.env.POWERCONTEXT_OPENAPI
  else process.env.POWERCONTEXT_OPENAPI = originalEnv.POWERCONTEXT_OPENAPI
  if (originalEnv.POWERCONTEXT_ROOT === undefined) delete process.env.POWERCONTEXT_ROOT
  else process.env.POWERCONTEXT_ROOT = originalEnv.POWERCONTEXT_ROOT
})

describe('resolveOpenApiPath', () => {
  it('prefers POWERCONTEXT_OPENAPI when the file exists', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pc-openapi-'))
    const yamlPath = join(dir, 'powercontext.yaml')
    writeFileSync(yamlPath, 'openapi: 3.1.0\n')
    process.env.POWERCONTEXT_OPENAPI = yamlPath
    expect(resolveOpenApiPath()).toBe(yamlPath)
  })

  it('uses POWERCONTEXT_ROOT/openapi/powercontext.yaml next', () => {
    const root = mkdtempSync(join(tmpdir(), 'pc-root-'))
    mkdirSync(join(root, 'openapi'))
    const yamlPath = join(root, 'openapi', 'powercontext.yaml')
    writeFileSync(yamlPath, 'openapi: 3.1.0\n')
    delete process.env.POWERCONTEXT_OPENAPI
    process.env.POWERCONTEXT_ROOT = root
    expect(resolveOpenApiPath()).toBe(yamlPath)
    expect(resolvePowerContextRoot()).toBe(root)
  })

  it('prefers the repository contract over the plugin fallback', () => {
    const checkout = mkdtempSync(join(tmpdir(), 'pc-checkout-'))
    const pluginRoot = join(checkout, 'integrations', 'dsh', 'plugins', 'powercontext')
    const repositoryYaml = join(checkout, 'openapi', 'powercontext.yaml')
    const fallbackYaml = join(pluginRoot, 'openapi', 'powercontext.yaml')
    mkdirSync(join(checkout, 'openapi'), { recursive: true })
    mkdirSync(join(pluginRoot, 'openapi'), { recursive: true })
    writeFileSync(repositoryYaml, 'openapi: 3.1.0\ninfo:\n  title: repository\n')
    writeFileSync(fallbackYaml, 'openapi: 3.1.0\ninfo:\n  title: fallback\n')
    clearOverrides()

    expect(resolvePowerContextRoot(pluginRoot)).toBe(checkout)
    expect(resolveOpenApiPath(pluginRoot)).toBe(repositoryYaml)
  })

  it('uses the plugin contract as a standalone fallback', () => {
    const pluginRoot = mkdtempSync(join(tmpdir(), 'pc-standalone-'))
    const fallbackYaml = join(pluginRoot, 'openapi', 'powercontext.yaml')
    mkdirSync(join(pluginRoot, 'openapi'))
    writeFileSync(fallbackYaml, 'openapi: 3.1.0\n')
    clearOverrides()

    expect(resolvePowerContextRoot(pluginRoot)).toBeUndefined()
    expect(resolveOpenApiPath(pluginRoot)).toBe(fallbackYaml)
  })
})
