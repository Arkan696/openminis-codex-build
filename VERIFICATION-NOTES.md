## OpenMinis Codex overlay — local verification record

- Target: iOS 16.5 / TrollStore, independent CodexSessionEngine.
- Upstream: `1a71df0f381cefde80bf912b723de6f9831bc177`.
- Production source in openminis-research and Codex Pocket was not modified.
- No user Codex executable was run, copied into this archive, uploaded, or signature-authenticated.

Completed locally:

1. Objective-C host strict iOS16.5 syntax check with Clang19 and Theos SDK (SDK metadata16.4).
2. C framer iOS ARM64 object compile.
3. C framer executed as local shared ELF through Python ctypes; includes CRLF, chunk/byte UTF-8 boundaries, line/total bounds and unfinished-tail rejection.
4. Reviewed ephemeral TOML parsed with Python tomllib; native guard contains its fixed SHA256.
5. Thirteen unittest cases executed: pinned blob identities, deterministic patch, OpenStep PBX parser, five Sources/two headers/one runtime folder, unchanged existing targets/configurations, source application/no-write plan, repeat refusal, rollback on injected failure, wrong commit/baseline refusal, Mach-O device-platform checking, profile/CA/symlink refusal, local-use acknowledgement, bounded/path-safe tar member handling, complete synthetic deb pipeline without execution.
6. Swift source review found no proven first-login/first-turn deterministic failure, but review is not a compiler test.
7. Nine local Apple-validation-tool tests executed: generated PBX object graph and target/module/test-host linkage; shared scheme; exact production source copying; default plan/no write/no Xcode; refusal to execute on Linux; explicit booted iOS simulator requirement; compiler-failure report; zero/partial XCTest result refusal; missing xcresult never counted as success. These are Python tests of the generator/runner, not Apple compilation.
8. Four additional Swift regression scenarios were written (interrupt terminal before ACK, delayed native stop, early login completion, wrong thread/turn), bringing the fixture to 14 test methods. NONE has executed in XCTest.

Total executed Python cases: 22. A mock-only standalone Apple project can now be generated without iSH/FFmpeg/rclone; see APPLE-VALIDATION.md. No runnable Swift compiler has been verified on the current Alpine/musl host. Do not count the static Linux SDK or project generation as typecheck.

Not completed:

- Swift typecheck, SwiftUI rendering, XCTest execution.
- Xcode project open/build, full OpenMinis native dependency build.
- Any Codex core execution, runtime signature authentication, TrollStore install or iPhone test.
- IPA/TIPA production.

`VERIFICATION.json` contains source digests and explicit false flags for the missing checks. Reproduce with `scripts/verify_local.py` and the pinned test dependency in `requirements-test.txt`. Synthetic runtime samples are only minimal Mach-O headers, not executable programs. PBX parsing and successful Git application do not establish that Xcode can build the complete App.
