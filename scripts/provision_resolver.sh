#!/usr/bin/env bash
# Keep the rootfs resolver exactly as supplied while lending host DNS to chroot.

prepare_provision_resolver() {
    PROVISION_RESOLVER_PATH="$1/etc/resolv.conf"
    PROVISION_RESOLVER_BACKUP="$1/etc/resolv.conf.ark-provision-backup"
    PROVISION_RESOLVER_ACTIVE=0
    PROVISION_RESOLVER_WAS_ABSENT=0
    if [ -e "$PROVISION_RESOLVER_BACKUP" ] || [ -L "$PROVISION_RESOLVER_BACKUP" ]; then
        echo "ERROR: stale provisioning resolver backup: $PROVISION_RESOLVER_BACKUP" >&2
        return 1
    fi
    if [ -d "$PROVISION_RESOLVER_PATH" ]; then
        echo "ERROR: rootfs resolv.conf is a directory: $PROVISION_RESOLVER_PATH" >&2
        return 1
    fi
    # Mark active before moving so the EXIT trap can recover an interrupted mv.
    PROVISION_RESOLVER_ACTIVE=1
    if [ -e "$PROVISION_RESOLVER_PATH" ] || [ -L "$PROVISION_RESOLVER_PATH" ]; then
        sudo mv -- "$PROVISION_RESOLVER_PATH" "$PROVISION_RESOLVER_BACKUP" || return
    else
        PROVISION_RESOLVER_WAS_ABSENT=1
    fi
    # The destination is absent, including on Noble's dangling /run symlink.
    sudo cp -- "${2:-/etc/resolv.conf}" "$PROVISION_RESOLVER_PATH"
}

restore_provision_resolver() {
    [ "${PROVISION_RESOLVER_ACTIVE:-0}" -eq 1 ] || return 0
    if [ -e "$PROVISION_RESOLVER_BACKUP" ] || [ -L "$PROVISION_RESOLVER_BACKUP" ]; then
        sudo rm -f -- "$PROVISION_RESOLVER_PATH" || return
        sudo mv -- "$PROVISION_RESOLVER_BACKUP" "$PROVISION_RESOLVER_PATH" || return
    elif [ "$PROVISION_RESOLVER_WAS_ABSENT" -eq 1 ]; then
        sudo rm -f -- "$PROVISION_RESOLVER_PATH" || return
    fi
    PROVISION_RESOLVER_ACTIVE=0
}
