# Changelog

## [0.4.0](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.3.0...v0.4.0) (2026-08-29)


### Features

* add `ghspot stats` ([67e0317](https://github.com/tguisep/gh-spot-docker-runners/commit/67e031701b903b37e914c214374f7e522d9cda29))
* **api:** serve the same report at GET /stats ([9a9e58e](https://github.com/tguisep/gh-spot-docker-runners/commit/9a9e58e6ef8c35448ed8e530db0e287ede1ff9ee))
* **cli:** add `ghspot stats` ([b8ab06e](https://github.com/tguisep/gh-spot-docker-runners/commit/b8ab06eaa8fb667f3827662219bf024cbd2d609b))
* **config:** read pm, and refuse the keys it does not use ([846b2b3](https://github.com/tguisep/gh-spot-docker-runners/commit/846b2b3483ab1b8dd8ae2c474a9477a5de864db6))
* **core:** fold the event log into usage statistics ([9cea2f0](https://github.com/tguisep/gh-spot-docker-runners/commit/9cea2f0759e5a1065094d509179b121c1db3e012))
* **domain:** name how a pool keeps its runners, as php-fpm's pm ([98027d3](https://github.com/tguisep/gh-spot-docker-runners/commit/98027d316be548ce391d4879a0d29b7cbe456dbf))
* **domain:** record the pool, and read the log by time ([783b7f1](https://github.com/tguisep/gh-spot-docker-runners/commit/783b7f109ce3b88dcd78d6febb84961b216510a6))
* how a pool keeps its runners, as php-fpm's pm ([b3283de](https://github.com/tguisep/gh-spot-docker-runners/commit/b3283de0ce26392be0e7b791a9c9ab4d2fef6e5c))
* **runner-image:** install uv, uvx and mise ([ec95e80](https://github.com/tguisep/gh-spot-docker-runners/commit/ec95e8080b2ec4d804ac6fe75a1724ea47201c31))
* **runner-image:** install uv, uvx and mise ([b3be5b7](https://github.com/tguisep/gh-spot-docker-runners/commit/b3be5b7c9748a70179664c9712bcc6e9db6e2b6e))


### Refactoring

* **config:** promote the duration parser ([c17b205](https://github.com/tguisep/gh-spot-docker-runners/commit/c17b205394647dbfce2f9f440419669c15d6bab7))


### Documentation

* document `ghspot stats` ([a6ce115](https://github.com/tguisep/gh-spot-docker-runners/commit/a6ce11552e67e204626023e1a03d1b5a1b4746e6))
* document the pm modes ([96a2cbf](https://github.com/tguisep/gh-spot-docker-runners/commit/96a2cbf3a0c0463ca69a2cb2fbb2a3b02c8fa132))
* drop the sentences that only announce importance ([b6d937f](https://github.com/tguisep/gh-spot-docker-runners/commit/b6d937f5dfe0065e618144dfb24f2648ad34b49a))
* drop the sentences that only announce importance ([1ea1a2a](https://github.com/tguisep/gh-spot-docker-runners/commit/1ea1a2a056cd60f9d08cde41c6c7d785e742dacd))
* **readme:** trade rhetorical cadence for plain statements ([59d0a05](https://github.com/tguisep/gh-spot-docker-runners/commit/59d0a05bafc022f6278041702aaa997ed6ed09e3))
* **readme:** trade rhetorical cadence for plain statements ([6322f77](https://github.com/tguisep/gh-spot-docker-runners/commit/6322f77ca70405131f4dea853ffda6870fb000f3))
* use the project contact address in the code of conduct ([1215069](https://github.com/tguisep/gh-spot-docker-runners/commit/12150694975e3fab27e84c9936f4077db90f1fc2))


### Build and CI

* **deps:** bump the actions group with 5 updates ([af064a8](https://github.com/tguisep/gh-spot-docker-runners/commit/af064a80a665bb85c88c0882e2d2dbef0b1a4358))
* **deps:** bump the actions group with 5 updates ([46ccaae](https://github.com/tguisep/gh-spot-docker-runners/commit/46ccaae686d5c935fbbc9fc8e7ad6a2d041a4656))
* let the ansible render check print its report ([b7d4e72](https://github.com/tguisep/gh-spot-docker-runners/commit/b7d4e7245bff195201b3d287ccc94ee8b71ce7af))
* pin every action to a commit ([c6bd614](https://github.com/tguisep/gh-spot-docker-runners/commit/c6bd614ed13501c1f74ae3d5bca4ba710f4741b6))

## [0.3.0](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.2.1...v0.3.0) (2026-08-27)


### Features

* **runner-image:** install the GitHub CLI ([27ba9d7](https://github.com/tguisep/gh-spot-docker-runners/commit/27ba9d76a6bd186474a6d43451cae1490a59814a))
* **runner-image:** install the GitHub CLI ([d9fea59](https://github.com/tguisep/gh-spot-docker-runners/commit/d9fea59fee60537092c5296e6bffb00006165825))

## [0.2.1](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.2.0...v0.2.1) (2026-08-27)


### Fixes

* **ci:** tell gh which repository it is releasing to ([3ad68fe](https://github.com/tguisep/gh-spot-docker-runners/commit/3ad68fea98f2a3b3702783bfc4f5f2d18e497dc4))
* **ci:** tell gh which repository it is releasing to ([79f39a9](https://github.com/tguisep/gh-spot-docker-runners/commit/79f39a9e4ed19b31ff83095ca76263e53cbaf620))

## [0.2.0](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.1.0...v0.2.0) (2026-08-27)


### Features

* **api:** add the REST API ([1ebf232](https://github.com/tguisep/gh-spot-docker-runners/commit/1ebf232560f05ff61dbb8ff4877c78039b9de6d5))
* **application:** add provisioning, retirement and the reconciliation loop ([214dfa0](https://github.com/tguisep/gh-spot-docker-runners/commit/214dfa075a9d14dedf9588b84c02e921db45ad34))
* **application:** move runner resolution out of the CLI ([7f4448a](https://github.com/tguisep/gh-spot-docker-runners/commit/7f4448a64e299f0067cc54d3b635eb633a64548c))
* **cli:** add the operator interface ([10b2a05](https://github.com/tguisep/gh-spot-docker-runners/commit/10b2a056c4d46c2aa8d23dd46ee879562bb4d4cc))
* **cli:** check the GPU toolkit when a pool asks for one ([f0f514f](https://github.com/tguisep/gh-spot-docker-runners/commit/f0f514f29e17d08f72f1f5f12833954bd42185f1))
* **config:** load and validate configuration ([7df566b](https://github.com/tguisep/gh-spot-docker-runners/commit/7df566bed3ae25a0b899609b3cc28110008587db))
* **config:** select the authentication mode from configuration ([74a0185](https://github.com/tguisep/gh-spot-docker-runners/commit/74a0185a1e10bce6d1a32657760eff161ef13226))
* **daemon:** add the composition root and the reconciliation loop ([b608a8c](https://github.com/tguisep/gh-spot-docker-runners/commit/b608a8c5d077f91a5b0461023ff32db1f9748d6e))
* **daemon:** run housekeeping on a schedule ([56a2f18](https://github.com/tguisep/gh-spot-docker-runners/commit/56a2f1848b5e9e7c3e6076a0576faee364e8fd21))
* **deploy:** add an Ansible role ([180e367](https://github.com/tguisep/gh-spot-docker-runners/commit/180e3678411f445b5c11a5d673d6fde8ec21d885))
* **deploy:** add an Ansible role ([e007ffe](https://github.com/tguisep/gh-spot-docker-runners/commit/e007ffe6d396e583bc92e42bb627fea6f2cec00c))
* **deploy:** expose housekeeping through the role ([02530bb](https://github.com/tguisep/gh-spot-docker-runners/commit/02530bb2f1872c681a60cd89908876122fc68133))
* **docker:** add the container backend ([74ae46c](https://github.com/tguisep/gh-spot-docker-runners/commit/74ae46c983a37536b891d4bc8e8909294aa86d4b))
* **docker:** let a pool hand its jobs the host's GPUs ([534c580](https://github.com/tguisep/gh-spot-docker-runners/commit/534c580c0fa0c3e1df1d8f4773496b4f259fe286))
* **docker:** reclaim what jobs leave on the host ([2b9d9a6](https://github.com/tguisep/gh-spot-docker-runners/commit/2b9d9a6110ae9658eaae9468e85397bdac77d2d3))
* **domain:** add forge and backend error types ([ad26e35](https://github.com/tguisep/gh-spot-docker-runners/commit/ad26e35d7d47fa45fe9049cf4215be1d6a303750))
* **domain:** add the runner lifecycle model and scaling policy ([7375f3c](https://github.com/tguisep/gh-spot-docker-runners/commit/7375f3c199998c7c6024624b42af9cba05787211))
* **domain:** allow an unknown job id when a runner is observed busy ([2d5033d](https://github.com/tguisep/gh-spot-docker-runners/commit/2d5033df36b2a2aeaef6f36007eed04da6b0e574))
* **domain:** implement requires_labels ([28a4675](https://github.com/tguisep/gh-spot-docker-runners/commit/28a4675029ea5b9f7c56a0e33edc77c327a221c5))
* **domain:** let a pool demand its labels be asked for by name ([c230414](https://github.com/tguisep/gh-spot-docker-runners/commit/c230414c27c3bce3e40281db657febd59e37ab30))
* **github:** add the REST client ([7873e15](https://github.com/tguisep/gh-spot-docker-runners/commit/7873e151732105d722a1563c6ec17d778cab7d31))
* **github:** authenticate as a GitHub App ([150d6ee](https://github.com/tguisep/gh-spot-docker-runners/commit/150d6ee3753e1227623ae70ad865756f4b3b8825))
* **github:** authenticate as a GitHub App ([02d9fce](https://github.com/tguisep/gh-spot-docker-runners/commit/02d9fce087834dc118c96cd03a38559f26d2de03))
* GPU support for runner pools ([be0fa44](https://github.com/tguisep/gh-spot-docker-runners/commit/be0fa448668ec1d55b595e6879f4bb3b5b2b389c))
* land the runner daemon (phases 2-9) ([34ce113](https://github.com/tguisep/gh-spot-docker-runners/commit/34ce1131eab24dcebe9f3393434fd5fd199410b6))
* **packaging:** build a .deb and publish it on tags ([77eb328](https://github.com/tguisep/gh-spot-docker-runners/commit/77eb3286d0f33db24d95f5940e80af4665ef4a64))
* **packaging:** build a Debian package ([4f87c1c](https://github.com/tguisep/gh-spot-docker-runners/commit/4f87c1c74c93afc7d28ad7059dab93862be0943e))
* **persistence:** add the SQLite projection ([9126580](https://github.com/tguisep/gh-spot-docker-runners/commit/9126580b4bd8cc247e15ed8b827b579ed4caf785))
* reclaim what jobs leave on the host ([3711ce8](https://github.com/tguisep/gh-spot-docker-runners/commit/3711ce825d1be14fae20c0d40c1e1b44393adaf6))
* **runner-image:** add RHEL-family images and version the variants ([38625a3](https://github.com/tguisep/gh-spot-docker-runners/commit/38625a335877af1c6f6f75c91707aaa2b4aa0013))
* **runner-image:** add the runner image and its entrypoint ([7600768](https://github.com/tguisep/gh-spot-docker-runners/commit/76007685e834942cf64a246b650983c6c4ea5f72))
* **runner-image:** install pipx ([bf256a6](https://github.com/tguisep/gh-spot-docker-runners/commit/bf256a6aaff280ff34636831bf6c6b19c8d45c3c))
* **runner-image:** install pipx, and stop CI clobbering the fleet's images ([0508405](https://github.com/tguisep/gh-spot-docker-runners/commit/0508405a619aa191018ec2721d127701bab617a0))
* **runner-image:** install the toolset GitHub ships on its own runners ([0fb73e7](https://github.com/tguisep/gh-spot-docker-runners/commit/0fb73e7d07759c9e975dff0b2571249054e0ec36))
* **runner-image:** install the toolset GitHub ships on its own runners ([6393816](https://github.com/tguisep/gh-spot-docker-runners/commit/6393816c9a192543676205e1538edb6078577ba9))
* **runner-image:** pin the upstream toolset and report drift ([c91e909](https://github.com/tguisep/gh-spot-docker-runners/commit/c91e90938174fa5162b56faf02b8086fdf148d8b))
* **runner-image:** pin the upstream toolset and report drift ([e9752e3](https://github.com/tguisep/gh-spot-docker-runners/commit/e9752e3d546eaaec507b6667dbd8f3faed3c68aa))
* **runner-image:** RHEL/CentOS support and versioned OS labels ([f196ad0](https://github.com/tguisep/gh-spot-docker-runners/commit/f196ad00ca287a28552af038e776578b02435626))


### Fixes

* **ci:** recover the rhel-10 CPU guard and hosted image builds ([9743478](https://github.com/tguisep/gh-spot-docker-runners/commit/97434785d1f9006e322c694c0a1f3472ee7237c3))
* **cli:** doctor now finishes its report when Docker is unreachable ([84530ef](https://github.com/tguisep/gh-spot-docker-runners/commit/84530eff11e67625f7b0cc156464e2d74eb5df04))
* **cli:** let doctor finish its report when Docker is unreachable ([288f380](https://github.com/tguisep/gh-spot-docker-runners/commit/288f380343cfcb7f027412508ddb7214f61949d5))
* **cli:** report a missing credential from the daemon without a traceback ([26d43b9](https://github.com/tguisep/gh-spot-docker-runners/commit/26d43b9a089ea8dfd01ec072d3412eac0c89a545))
* **deploy:** stop the unit failing before the daemon can explain why ([87f29cc](https://github.com/tguisep/gh-spot-docker-runners/commit/87f29cc93245e6500e02b31274b4e541a9200739))
* **deploy:** stop the unit failing before the daemon can explain why ([4663764](https://github.com/tguisep/gh-spot-docker-runners/commit/46637649fd9aa6e77da175543739775820a4e78c))
* **docker:** point the missing-image error at the build script ([2ee04b9](https://github.com/tguisep/gh-spot-docker-runners/commit/2ee04b959bc89c0e576ff5daa1c9acda2e5ad35e))
* **packaging:** stop .gitignore swallowing the shipped config ([4d78804](https://github.com/tguisep/gh-spot-docker-runners/commit/4d788045c5aa6bd8dfb368222d0acb0cd9291403))
* **runner-image:** check the rhel images against upstream too ([fea972e](https://github.com/tguisep/gh-spot-docker-runners/commit/fea972e49f7f6281915f22ef6fb4855dff964b46))
* **runner-image:** say why rhel-10 will not build on an older CPU ([d6b6dbc](https://github.com/tguisep/gh-spot-docker-runners/commit/d6b6dbcbb1661e02ed3a1cceb2efe5629551639d))


### Performance

* **application:** launch a burst together rather than one at a time ([f68a650](https://github.com/tguisep/gh-spot-docker-runners/commit/f68a6506b07091ebc840b6c55eb8750c997a02fa))
* **github:** fetch job listings concurrently ([a16d0a6](https://github.com/tguisep/gh-spot-docker-runners/commit/a16d0a650a58b9215639f0263ea529ca04ce55f5))
* stop a tick taking minutes to read the queue ([e0d9c4c](https://github.com/tguisep/gh-spot-docker-runners/commit/e0d9c4c3f46d592a9b6bf4ccd2e82358342aef8f))


### Documentation

* add a quick start, contributing guide and project history ([dbabf45](https://github.com/tguisep/gh-spot-docker-runners/commit/dbabf457941c4a896623a60d33b2d54b2dbb934e))
* add a setup guide for tokens and GitHub Apps ([dc08dcf](https://github.com/tguisep/gh-spot-docker-runners/commit/dc08dcf395fd317ad035112dc46c4df10261e46c))
* add architecture, operations and decision records ([b900c02](https://github.com/tguisep/gh-spot-docker-runners/commit/b900c0254a94bb50db020aeed307b7b1af7e4bd9))
* architecture, operations, ADRs and security ([b4e0672](https://github.com/tguisep/gh-spot-docker-runners/commit/b4e0672cad3eff7205aab96f32e74badeab7844c))
* document GitHub App authentication ([11fb5d9](https://github.com/tguisep/gh-spot-docker-runners/commit/11fb5d9c8c6f6708efa47a75cf8b20b3572d3c4b))
* document GPU pools across every deployment path ([3486f01](https://github.com/tguisep/gh-spot-docker-runners/commit/3486f01672323f62fbdfac0ad0464e63e553cf91))
* document how releases are cut ([8056e52](https://github.com/tguisep/gh-spot-docker-runners/commit/8056e520e39b99b793572405d126680f1b3eb9ff))
* document the runner variants and specific OS labels ([5f49343](https://github.com/tguisep/gh-spot-docker-runners/commit/5f49343661095da6af379cb35b068093c63ab653))
* fix the GPU label guidance ([912c83c](https://github.com/tguisep/gh-spot-docker-runners/commit/912c83cca4c89e985ac9ee1fb4fbd1fbecbc633f))
* fix the install instructions ([c60f467](https://github.com/tguisep/gh-spot-docker-runners/commit/c60f467aca949b408d167378a8073c21adef64c5))
* fix the install instructions ([815f960](https://github.com/tguisep/gh-spot-docker-runners/commit/815f960c4cba0235cd201b713fffc3fe1df9b5b6))
* record that deployment paths must be kept in step ([8ebba09](https://github.com/tguisep/gh-spot-docker-runners/commit/8ebba099ce1b638c9dbdd5d88d1c21257849344b))
* record what the runner images carry, and what they do not ([1c68c01](https://github.com/tguisep/gh-spot-docker-runners/commit/1c68c01e757bc1a84098f262a03218ddbbdcbf7c))
* say plainly what housekeeping does and does not guarantee ([c08fcda](https://github.com/tguisep/gh-spot-docker-runners/commit/c08fcdacd7e1aeee1d140edd903bb8fa40c1047c))
* setup guide for tokens and GitHub Apps ([832858d](https://github.com/tguisep/gh-spot-docker-runners/commit/832858dd4b2cc37ea449950042f2cedebd101122))
* the service install needs python3-venv ([f6f1e89](https://github.com/tguisep/gh-spot-docker-runners/commit/f6f1e89b26709298e97ec7c349c025c00fefea61))


### Build and CI

* add lint, type-check and test workflow ([c0efdee](https://github.com/tguisep/gh-spot-docker-runners/commit/c0efdee1047dcb27e0744573adb6edd310a90659))
* ask weekly whether upstream has tooling we lack ([60781e7](https://github.com/tguisep/gh-spot-docker-runners/commit/60781e7516190e80391ed110511a42e58c2ff39e))
* build and check every runner variant ([2e4dcfb](https://github.com/tguisep/gh-spot-docker-runners/commit/2e4dcfbf9dc20975d3b7527ca19e2ab0c4244321))
* build runner images on hosted runners ([56e72ee](https://github.com/tguisep/gh-spot-docker-runners/commit/56e72eea0d44b1e2939e37a2e59a0d91f0da67d8))
* build runner images under a name the fleet does not use ([906472b](https://github.com/tguisep/gh-spot-docker-runners/commit/906472bbaac317b591bae250a60cc6f942bd1783))
* check the contract and the toolset of every runner image ([5897824](https://github.com/tguisep/gh-spot-docker-runners/commit/5897824b2678b8be0cfe973d876d6b63c4a41580))
* cut releases automatically with release-please ([afcbd07](https://github.com/tguisep/gh-spot-docker-runners/commit/afcbd078a94bb4a83e96a801f42f6fd8b8ca067a))
* cut releases with release-please ([2e42f26](https://github.com/tguisep/gh-spot-docker-runners/commit/2e42f26693330eb488791d72c2af84e301a4fc0b))
* give the release pull request a stable title ([5880adf](https://github.com/tguisep/gh-spot-docker-runners/commit/5880adf6d0ef2a5f938de18b4cbe0cd28bde00e4))
* run on the self-hosted fleet, except for fork pull requests ([b39a118](https://github.com/tguisep/gh-spot-docker-runners/commit/b39a118f2359ad6377c30c4ccab2214adb8affe9))
* run on the self-hosted fleet, except for fork pull requests ([c237e0c](https://github.com/tguisep/gh-spot-docker-runners/commit/c237e0c1f4b88206009767c49177f76cf4dbf635))
* run the documented install on a clean system ([09d91af](https://github.com/tguisep/gh-spot-docker-runners/commit/09d91afc9efef62886d752aed09386c732a0df4a))
