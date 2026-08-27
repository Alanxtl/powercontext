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

import { describe, expect, it } from 'vitest'
import { failureEvent } from './diagnostics.js'
import { PowerContextRequestError } from './http.js'

describe('host-visible diagnostic classification', () => {
  it('uses version_mismatch only for compatibility or availability endpoints', () => {
    expect(failureEvent('context_prepare', new PowerContextRequestError(
      '/v1/context/prepare',
      'missing endpoint',
      404,
    ))).toEqual({ event: 'context_prepare', outcome: 'version_mismatch', http_status: 404 })

    expect(failureEvent('capture_source', new PowerContextRequestError(
      '/v1/memory/entries/get',
      'missing entry',
      404,
    ))).toBeUndefined()
  })

  it('does not emit availability diagnostics for direct domain errors', () => {
    for (const status of [404, 409, 422]) {
      expect(failureEvent('tool_call', new PowerContextRequestError(
        '/v1/memory/entries/get',
        'domain error',
        status,
      ))).toBeUndefined()
    }
  })
})
