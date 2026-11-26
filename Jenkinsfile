/*
 * Copyright (c) 2026 MirraNET, Niklas Linz. All rights reserved.
 *
 * This file is part of the MirraNET project and is licensed under the
 * GNU Lesser General Public License v3.0 (LGPLv3).
 *
 * You may use, distribute and modify this code under the terms
 * of the LGPLv3 license. You should have received a copy of the
 * license along with this file. If not, see <https://www.gnu.org/licenses/lgpl-3.0.html>
 * or contact: niklas.linz@mirranet.de
 */

// FreeXHT: tests, the package freexht (Architecture: all) and the system images for the Raspberry
// Pi (3/4/5, arm64) and x86_64 PCs.
//
// The interface comes from FreeX-TV-Apps (web/), branch TV_APPS_REF (default main).
//
// The build agent needs Debian 13 or newer with (once, as root):
//
//   sudo apt install python3 dpkg-dev git curl gpg mmdebstrap uidmap e2fsprogs libarchive13t64 dosfstools mtools \
//       fdisk xz-utils qemu-user-static binfmt-support arch-test debian-archive-keyring
//   sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 jenkins
//
// mmdebstrap builds the root file systems without root in a user namespace (the subordinate ids
// above); the Raspberry Pi image (arm64) is built through qemu-user. One image takes 5 to 15
// minutes and about 4 GB of space in the workspace's temp directory. A local Debian mirror or
// apt-cacher-ng speeds it up: set DEBIAN_MIRROR, SECURITY_MIRROR and RPI_MIRROR (the images still
// use the official sources).
//
// IMAGES=false (environment or job parameter) builds only the package.
//
// Releases, the GitHub release and the APT repository (apt.mirranet.de) are made by GitHub
// Actions (.github/workflows); Jenkins builds and archives the package and the images.

pipeline {
    agent any

    options {
        buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '5'))
        disableConcurrentBuilds()
        timeout(time: 90, unit: 'MINUTES')
    }

    stages {
        stage('Prepare') {
            steps {
                sh '''
                    rm -rf dist build
                    mkdir -p dist build
                    git clone -q --depth 1 --branch "${TV_APPS_REF:-main}" \
                        https://github.com/FreeX-Studio/FreeX-TV-Apps.git build/tv-apps
                    echo "FreeX TV interface: $(git -C build/tv-apps log -1 --format='%h %s')"
                '''
            }
        }

        stage('Test') {
            steps {
                sh 'python3 -m unittest discover -s tests -v'
            }
        }

        stage('Package') {
            steps {
                sh '''
                    VERSION="$(cat VERSION)"
                    # Every build gets its own package version, newer than the one before, so the
                    # boxes see it as an update: 0.1.0+202611151030.git1a2b3c4
                    COMMIT="$(git rev-parse --short=7 HEAD 2>/dev/null || echo local)"
                    echo "$VERSION+$(date -u +%Y%m%d%H%M).git$COMMIT" > build/deb-version
                    packaging/build-deb.sh "$(cat build/deb-version)" build/tv-apps/web dist
                '''
            }
        }

        stage('Images') {
            when {
                expression { env.IMAGES != 'false' }
            }
            steps {
                sh '''
                    VERSION="$(cat VERSION)"
                    DEB="$(ls dist/freexht_*_all.deb)"
                    image/build-image.sh rpi "$VERSION" "$DEB" dist
                    image/build-image.sh x86_64 "$VERSION" "$DEB" dist
                '''
            }
        }

    }

    post {
        success {
            archiveArtifacts artifacts: 'dist/*', fingerprint: true, allowEmptyArchive: false
        }
    }
}
