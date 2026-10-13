# Maintainer: Daskey <218856630+DerKyberTakt@users.noreply.github.com>
#
# Release package: builds from the tagged GitHub tarball.
#   makepkg -si
# After tagging a new release, refresh the checksum with:
#   updpkgsums && makepkg --printsrcinfo > .SRCINFO
# For a build from the working tree instead, use:  sudo make install
# For the rolling AUR package, see aur/ocypus-a62-linux-git/PKGBUILD.

pkgname=ocypus-a62-linux
pkgver=0.2.0
pkgrel=1
pkgdesc="Show CPU temperature on the Ocypus IOTA A62 cooler display (hwmon to hidraw, no Ocypus Digital needed)"
arch=('any')
url="https://github.com/DerKyberTakt/ocypus-a62-linux"
license=('MIT')
depends=('python>=3.11' 'systemd')
optdepends=('lm_sensors: inspect available temperature sensors with sensors(1)')
install=ocypus-a62.install
source=("$pkgname-$pkgver.tar.gz::$url/archive/v$pkgver.tar.gz")
# Replaced by updpkgsums once the v$pkgver tag exists on GitHub.
sha256sums=('SKIP')

build() {
  cd "$pkgname-$pkgver"
  make BINDIR=/usr/bin build
}

check() {
  cd "$pkgname-$pkgver"
  make check
}

package() {
  cd "$pkgname-$pkgver"
  make DESTDIR="$pkgdir" PREFIX=/usr UDEVDIR=/usr/lib/udev/rules.d install
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
