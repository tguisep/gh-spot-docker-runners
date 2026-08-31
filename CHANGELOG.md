# Changelog

## [0.6.0](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.5.1...v0.6.0) (2026-08-31)


### Fixes

* **ci:** build the release commit, not its tag ([d9e90f7](https://github.com/tguisep/gh-spot-docker-runners/commit/d9e90f7b9a13f0056f5f191c8083378a4e156d36))

## [0.5.1](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.5.0...v0.5.1) (2026-08-31)


### Fixes

* **ci:** attach the packages before the release is sealed ([c2e367e](https://github.com/tguisep/gh-spot-docker-runners/commit/c2e367efa8d60fd71e0276633af29a2b3e895ccf))
* **ci:** attach the packages before the release is sealed ([e0fa535](https://github.com/tguisep/gh-spot-docker-runners/commit/e0fa5354c1017f9d5a7653f4035943e29a7d0104))

## [0.5.0](https://github.com/tguisep/gh-spot-docker-runners/compare/v0.4.0...v0.5.0) (2026-08-31)


### Features

* **ansible:** render daemon.host from ghspot_host ([17168af](https://github.com/tguisep/gh-spot-docker-runners/commit/17168afaa89c52572f3fbc7e1fa5e68a20ee845f))
* **api:** report the host from /health and /stats ([b67e619](https://github.com/tguisep/gh-spot-docker-runners/commit/b67e6190af2d9d504282e2051f7a1a8693d29cc5))
* **api:** say when the configuration on disk has moved on ([e6d3438](https://github.com/tguisep/gh-spot-docker-runners/commit/e6d3438bc5eebac8e9c8c4a6af13228b87a5bbcc))
* **api:** serve archived logs, and say when there are none ([e4ae9eb](https://github.com/tguisep/gh-spot-docker-runners/commit/e4ae9eb17b62ca5a08335f79a94b399b79133458))
* build the runner images from ghspot itself ([a9073d0](https://github.com/tguisep/gh-spot-docker-runners/commit/a9073d0f6eab2426d24e6db2ed9cb207eea3cc10))
* **cli:** add ghspot image build ([80ce434](https://github.com/tguisep/gh-spot-docker-runners/commit/80ce4342548acc6a482fcd2dc885505318f639ab))
* **cli:** ghspot runner stop --all ([f0cfc28](https://github.com/tguisep/gh-spot-docker-runners/commit/f0cfc284f83b85d8270fe798144f30cf236417f6))
* **cli:** ghspot runner stop --all ([c7027c6](https://github.com/tguisep/gh-spot-docker-runners/commit/c7027c6e061d8c9435d001aa9d3fe3146fd30d47))
* **cli:** offer to build the runner image from the wizard ([019001f](https://github.com/tguisep/gh-spot-docker-runners/commit/019001f3f0b71b60c8ea92fe23384303624ec010))
* **cli:** put the host in stats and doctor ([0dede92](https://github.com/tguisep/gh-spot-docker-runners/commit/0dede922ad138b78d6d09243c6ff557552f8ea5c))
* **cli:** the wizard writes the full configuration, offers the build, and grants nothing by default ([9f072ad](https://github.com/tguisep/gh-spot-docker-runners/commit/9f072ad7119ab4216130fa26f5dd752bfd2c0e85))
* **cli:** write the whole commented configuration, not four answers ([dd5cdbb](https://github.com/tguisep/gh-spot-docker-runners/commit/dd5cdbbbe7b6e5ba815aa8db9e476bb101d363bc))
* **core:** capture the container log before removing it ([ce4e237](https://github.com/tguisep/gh-spot-docker-runners/commit/ce4e2374a98f7e72c989111072a8b7b65c2d1e58))
* **core:** keep the tail of a retired runner's container ([3a18c08](https://github.com/tguisep/gh-spot-docker-runners/commit/3a18c08b41a4b08b933fda547110413fa3183b84))
* **core:** name the machine a daemon reports for ([de40e2c](https://github.com/tguisep/gh-spot-docker-runners/commit/de40e2cd52f17953b5925790e0f90f40037ef013))
* **core:** stop takes the fleet with it, reload does not ([2430ab3](https://github.com/tguisep/gh-spot-docker-runners/commit/2430ab3d941bf7c8d01672e95958687996dc680a))
* **dashboard:** call the force button "kill" ([383a0ac](https://github.com/tguisep/gh-spot-docker-runners/commit/383a0ac5bad8164b0c9765ad4efdeebcc273ff31))
* **dashboard:** call the force button "kill" ([5798135](https://github.com/tguisep/gh-spot-docker-runners/commit/5798135ed24d4da3c50d1758d59034e25952fca5))
* **dashboard:** name the host in the header and the overview ([6a939fa](https://github.com/tguisep/gh-spot-docker-runners/commit/6a939fa6dee2ccd24270e66d57890dd784d9adab))
* **dashboard:** point the setup screen at ghspot image build ([7d6efff](https://github.com/tguisep/gh-spot-docker-runners/commit/7d6efff1c12550951fc075bfe2734128bda2ade4))
* **dashboard:** show a retired runner's kept output ([fbbf02d](https://github.com/tguisep/gh-spot-docker-runners/commit/fbbf02d70a9fc7ef32beda524fc87c999c47e8f0))
* **deploy:** ExecReload, and an Ansible handler that uses it ([67f0451](https://github.com/tguisep/gh-spot-docker-runners/commit/67f0451e7fb32b36f3d53702e516130d9f979bc9))
* **github:** find the job a runner ran, by name ([5ffc4c0](https://github.com/tguisep/gh-spot-docker-runners/commit/5ffc4c08d81afa94c990e62e69e3d3a4296a5b79))
* **images:** let build.sh list its variants ([d675bc6](https://github.com/tguisep/gh-spot-docker-runners/commit/d675bc65fe9ca667b295644f3cb8cb9ca7c2db67))
* keep a retired runner's logs instead of losing them with the container ([2dd50d9](https://github.com/tguisep/gh-spot-docker-runners/commit/2dd50d97ebf9924ab4bdbaf3900f332b86d47158))
* **packaging:** ship the runner image sources in the .deb ([a4e00af](https://github.com/tguisep/gh-spot-docker-runners/commit/a4e00af04f2070e289ad7e0c1d847db4405033f7))
* say which host every report is about ([e45e103](https://github.com/tguisep/gh-spot-docker-runners/commit/e45e103ce317e128fa1c36b9b6374ee04b3fa6d6))
* stop takes the fleet with it, reload does not ([9d9936f](https://github.com/tguisep/gh-spot-docker-runners/commit/9d9936f357903b9487a7a329e1adb6beade6e2ea))


### Fixes

* **ansible:** assert the template adds no capacity limit of its own ([cfe1815](https://github.com/tguisep/gh-spot-docker-runners/commit/cfe1815252d820f703a95d46431cdbfb51fe264e))
* **ansible:** assert the template adds no capacity limit of its own ([c7ca46b](https://github.com/tguisep/gh-spot-docker-runners/commit/c7ca46b65265b4812c7db3b940d0803c99f68497))
* **cli:** default the wizard's two grants to no ([bc214e2](https://github.com/tguisep/gh-spot-docker-runners/commit/bc214e26994f798f56ad18b16f78bcd734395547))
* **cli:** make the job log reachable at all ([daf4b44](https://github.com/tguisep/gh-spot-docker-runners/commit/daf4b446b8f2906a9f4690847c0487868fcbe07e))
* **cli:** prefer the checkout over the installed copy ([d44470c](https://github.com/tguisep/gh-spot-docker-runners/commit/d44470c3fc4f2527a2c7a8030eda1208a50d2a5a))
* **cli:** write credentials the service account can read ([c370cd9](https://github.com/tguisep/gh-spot-docker-runners/commit/c370cd9bb06c3de26d6b6a7678aabe3b3bea04e4))
* **core:** stop warning about the credential layout the package creates ([729f9ca](https://github.com/tguisep/gh-spot-docker-runners/commit/729f9ca637b2ee1eababd719be0164214ced0d01))
* **deploy:** pin ConfigurationDirectoryMode to the mode the package creates ([11cef67](https://github.com/tguisep/gh-spot-docker-runners/commit/11cef67ee440d0fd0b574b52165c616f7ef1fb94))
* **docker:** point ImageNotFoundError at ghspot image build ([22c0e6c](https://github.com/tguisep/gh-spot-docker-runners/commit/22c0e6c40ad48ca6d60cbfc56f172f7cdc6df7ef))
* **images:** survive a host with no docker group ([d2902ee](https://github.com/tguisep/gh-spot-docker-runners/commit/d2902ee57156585c6028ea65a8ab529dc2f45acb))
* make a fresh apt install actually start, and ship the dashboard ([3418ee6](https://github.com/tguisep/gh-spot-docker-runners/commit/3418ee64fad23c6c9d4d86ba9b75ce58ac2f07ef))
* make the GitHub job log reachable at all ([e0e139c](https://github.com/tguisep/gh-spot-docker-runners/commit/e0e139cd5d973e1515caed742a83ed470c8bb3a1))
* **packaging:** do not check the variant list through grep -q ([7f818c5](https://github.com/tguisep/gh-spot-docker-runners/commit/7f818c5d02a4d7fd34e281a137e709e7beef4cc5))
* **packaging:** leave nothing behind on remove and purge ([e62c184](https://github.com/tguisep/gh-spot-docker-runners/commit/e62c184e0b8f3b8aa8909e3bbe0e5acd8a91fd48))
* **packaging:** put the dashboard in the released package ([a834ece](https://github.com/tguisep/gh-spot-docker-runners/commit/a834eceb61eee1495aff718b1f518d60b1f16e85))
* purge leftovers, a wrong credential warning, and silently stale settings ([81c42f1](https://github.com/tguisep/gh-spot-docker-runners/commit/81c42f1935bf4877ed45cc4f519b04c01eed0645))


### Refactoring

* **ansible:** build the runner images without a checkout ([3b5349f](https://github.com/tguisep/gh-spot-docker-runners/commit/3b5349fa6bdcb6fe4e18e6594841740229c059ae))


### Documentation

* cover the purge leftovers, the credential warning and stale settings ([2a6abd7](https://github.com/tguisep/gh-spot-docker-runners/commit/2a6abd760f75d9eebbab3e6fbe7ffe22b117b4c8))
* **dashboard:** say the job was not found, not that none was taken ([b44fb4e](https://github.com/tguisep/gh-spot-docker-runners/commit/b44fb4e23e513ac3e3931089933a9a8b7c9fc7e3))
* describe the configuration the wizard writes ([1bb8047](https://github.com/tguisep/gh-spot-docker-runners/commit/1bb80472edb8e7fd3550d78d74005eddb96b7679))
* describe the wizard's build offer ([530ba8a](https://github.com/tguisep/gh-spot-docker-runners/commit/530ba8a685f3184da05af5508e5a132fda759de4))
* document ghspot image build ([21b2175](https://github.com/tguisep/gh-spot-docker-runners/commit/21b2175f3423796d94e3606d06c6eb3fbf4db974))
* explain that stats are per host, not per fleet ([f4d9943](https://github.com/tguisep/gh-spot-docker-runners/commit/f4d9943dc10391cf22a5c7e5240bff268ac989cc))
* explain the settings rather than name php-fpm ([26fc9d8](https://github.com/tguisep/gh-spot-docker-runners/commit/26fc9d863cd8dba1793cf04b091067fa1d795243))
* explain what happens to a retired runner's logs ([fe3764c](https://github.com/tguisep/gh-spot-docker-runners/commit/fe3764c0a21e2dfc240f09df4aef7965c084bdcf))
* note that the wizard's two grants default to no ([acbae2a](https://github.com/tguisep/gh-spot-docker-runners/commit/acbae2aad5b47070f029250dcbd56d4934b938a9))
* point every reference at the site ([d1fc85c](https://github.com/tguisep/gh-spot-docker-runners/commit/d1fc85c2f928d3db0078a1e4184aa49ed29daea5))
* replace docs/ with an Astro site, grouped by domain ([3629fb2](https://github.com/tguisep/gh-spot-docker-runners/commit/3629fb25f947d8574bc22910b579f03d8795e93f))
* retire docs/ in favour of the site ([99ba9c1](https://github.com/tguisep/gh-spot-docker-runners/commit/99ba9c16c3dc06d4f90d11a91061fdf5632c8f7e))
* **site:** build the documentation with Astro and Starlight ([4444668](https://github.com/tguisep/gh-spot-docker-runners/commit/444466821a380df34c4a4d797d3c0e5be88c3864))
* **site:** group by domain, and cut the prose back ([6bc0146](https://github.com/tguisep/gh-spot-docker-runners/commit/6bc0146ce3e6979838976b65ccba476d077c0295))
* **site:** stop, restart and reload are three different things ([3bdef01](https://github.com/tguisep/gh-spot-docker-runners/commit/3bdef010b08e85f6074fdd96f5cb016115af6524))
* **site:** sub-pages for troubleshooting and architecture, and a schema page ([6b5ea01](https://github.com/tguisep/gh-spot-docker-runners/commit/6b5ea01f991716bc6003cac956eeb44627ba0972))
* stop, restart and reload are three different things ([bda9938](https://github.com/tguisep/gh-spot-docker-runners/commit/bda99380b2057249fee284a03e5d183646fba489))
* troubleshoot the unreadable credential and the missing dashboard ([6a3ac9e](https://github.com/tguisep/gh-spot-docker-runners/commit/6a3ac9e487f19050aaf0ae3927996f304da67ba1))

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
