/* Full original CRLF source SHA-256 at the pinned revision:
 * Silicon/NVIDIA/Library/DeviceDiscoveryLib/DeviceDiscoveryLib.c:
 * d232b4173f8fe45931220e67936e225bc50942f0f6497ea3933bb6e58c3aff4d
 * Silicon/NVIDIA/Library/DeviceDiscoveryDriverLib/DeviceDiscoveryDriverLib.c:
 * c153e0afa20632bf18c6586a7d70ae487c988a6262e9a3e5fc0e6e8008092d99
 * Only function extraction and newline normalization were performed.
 */
/* Extracted unchanged (newline-normalized) from NVIDIA edk2-nvidia
 * revision 79ad0c17aa4f13fdd5164d5b026fa49436b34891.
 * SPDX-FileCopyrightText: Copyright (c) 2018-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: BSD-2-Clause-Patent
 */
EFI_STATUS
GetSupportedDeviceTreeNodes (
  IN VOID                        *DeviceTreeBase,
  IN DEVICE_TREE_NODE_SUPPORTED  IsNodeSupported,
  IN OUT UINT32                  *DeviceCount,
  IN OUT NVIDIA_DT_NODE_INFO     *DTNodeInfo
  )
{
  EFI_STATUS           Status;
  VOID                 *DTBase;
  UINTN                DeviceTreeSize;
  CONST VOID           *Property    = NULL;
  INT32                PropertySize = 0;
  INT32                NodeOffset   = 0;
  UINT32               NodeCount    = 0;
  NVIDIA_DT_NODE_INFO  NodeInfo     = { NULL, 0, NULL, NULL };

  if ((IsNodeSupported == NULL) ||
      (DeviceCount == NULL) ||
      ((*DeviceCount != 0) && (DTNodeInfo == NULL)))
  {
    return EFI_INVALID_PARAMETER;
  }

  if (DeviceTreeBase == NULL) {
    Status = DtPlatformLoadDtb (&DTBase, &DeviceTreeSize);
    if (EFI_ERROR (Status)) {
      return EFI_DEVICE_ERROR;
    }
  } else {
    DTBase = DeviceTreeBase;
  }

  NodeInfo.DeviceTreeBase = DTBase;
  do {
    NodeOffset = fdt_next_node (DTBase, NodeOffset, NULL);
    if (NodeOffset < 0) {
      break;
    }

    NodeInfo.NodeOffset = NodeOffset;
    Status              = IsNodeSupported (&NodeInfo);
    if (EFI_ERROR (Status)) {
      continue;
    }

    Property = fdt_getprop (
                 DTBase,
                 NodeOffset,
                 "status",
                 &PropertySize
                 );
    if ((Property == NULL) ||
        (AsciiStrCmp (Property, "okay") == 0))
    {
      if (NodeCount < *DeviceCount) {
        NodeInfo.Phandle      = fdt_get_phandle (DTBase, NodeOffset);
        DTNodeInfo[NodeCount] = NodeInfo;
      }

      NodeCount++;
    }
  } while (NodeOffset > 0);

  if ((NodeCount > *DeviceCount) && (DTNodeInfo != NULL)) {
    Status = EFI_BUFFER_TOO_SMALL;
  } else if (NodeCount == 0) {
    Status = EFI_NOT_FOUND;
  } else {
    *DeviceCount = NodeCount;
    Status       = EFI_SUCCESS;
  }

  return Status;
}

EFI_STATUS
DeviceTreeIsSupported (
  IN NVIDIA_DEVICE_TREE_COMPATIBILITY_PROTOCOL  *This,
  IN CONST NVIDIA_DEVICE_TREE_NODE_PROTOCOL     *Node,
  OUT EFI_GUID                                  **DeviceType,
  OUT NON_DISCOVERABLE_DEVICE_INIT              *PciIoInitialize
  )
{
  NVIDIA_COMPATIBILITY_MAPPING  *MappingNode = gDeviceCompatibilityMap;

  if ((Node == NULL) ||
      (DeviceType == NULL) ||
      (PciIoInitialize == NULL))
  {
    return EFI_INVALID_PARAMETER;
  }

  while (MappingNode->Compatibility != NULL) {
    if (!EFI_ERROR (DeviceTreeCheckNodeSingleCompatibility (MappingNode->Compatibility, Node->NodeOffset))) {
      break;
    }

    MappingNode++;
  }

  if (MappingNode->Compatibility == NULL) {
    return EFI_UNSUPPORTED;
  }

  // Allow for NULL device type to reject matches
  if (MappingNode->DeviceType == NULL) {
    return EFI_UNSUPPORTED;
  }

  *DeviceType      = MappingNode->DeviceType;
  *PciIoInitialize = NULL;

  return DeviceDiscoveryNotify (
           DeviceDiscoveryDeviceTreeCompatibility,
           mImageHandle,
           NULL,
           Node
           );
}

EFI_STATUS
EnumerationIsNodeSupported (
  IN OUT  NVIDIA_DT_NODE_INFO  *DeviceInfo
  )
{
  NVIDIA_DEVICE_TREE_NODE_PROTOCOL  Node;

  if (DeviceInfo == NULL) {
    return EFI_INVALID_PARAMETER;
  }

  Node.DeviceTreeBase = DeviceInfo->DeviceTreeBase;
  Node.NodeOffset     = DeviceInfo->NodeOffset;

  return DeviceTreeIsSupported (NULL, &Node, &DeviceInfo->DeviceType, &DeviceInfo->PciIoInitialize);
}

EFI_STATUS
DeviceDiscoveryEnumerateDevices (
  VOID
  )
{
  EFI_STATUS               Status;
  NON_DISCOVERABLE_DEVICE  *Device;
  EFI_HANDLE               DeviceHandle;
  NVIDIA_DT_NODE_INFO      *DtNodeInfo;
  UINT32                   DeviceCount;
  UINT32                   Index;

  DeviceCount = 0;
  DtNodeInfo  = NULL;

  Status = GetSupportedDeviceTreeNodes (
             NULL,
             EnumerationIsNodeSupported,
             &DeviceCount,
             NULL
             );
  if (EFI_ERROR (Status) && (Status != EFI_NOT_FOUND)) {
    DEBUG ((DEBUG_ERROR, "%a: Failed to get supported nodes - %r\r\n", __FUNCTION__, Status));
    return Status;
  }

  if (!EFI_ERROR (Status)) {
    DtNodeInfo = (NVIDIA_DT_NODE_INFO *)AllocateZeroPool (DeviceCount * sizeof (NVIDIA_DT_NODE_INFO));
    if (DtNodeInfo == NULL) {
      DEBUG ((DEBUG_ERROR, "%a: Unable to allocate node structure\r\n", __FUNCTION__));
      return EFI_OUT_OF_RESOURCES;
    }

    Status = GetSupportedDeviceTreeNodes (
               NULL,
               EnumerationIsNodeSupported,
               &DeviceCount,
               DtNodeInfo
               );
    if (EFI_ERROR (Status)) {
      DEBUG ((DEBUG_ERROR, "%a: Failed to get supported nodes - %r\r\n", __FUNCTION__, Status));
      return Status;
    }
  } else {
    DeviceCount = 0;
  }

  for (Index = 0; Index < DeviceCount; Index++) {
    DeviceHandle = NULL;
    Device       = (NON_DISCOVERABLE_DEVICE *)AllocatePool (sizeof (NON_DISCOVERABLE_DEVICE));
    if (Device == NULL) {
      DEBUG ((DEBUG_ERROR, "%a: Failed to allocate device protocol.\r\n", __FUNCTION__));
      return EFI_DEVICE_ERROR;
    }

    Status = ProcessDeviceTreeNodeWithHandle (
               &DtNodeInfo[Index],
               Device,
               mImageHandle,
               &DeviceHandle
               );
    if (EFI_ERROR (Status)) {
      DEBUG ((DEBUG_ERROR, "%a: Failed to process device node - %r\r\n", __FUNCTION__, Status));
      continue;
    }

    Status = DeviceDiscoveryStart (
               DeviceHandle
               );
    if (EFI_ERROR (Status)) {
      continue;
    }
  }

  if (DtNodeInfo != NULL) {
    FreePool (DtNodeInfo);
    DtNodeInfo = NULL;
  }

  EnumerationCompleted = TRUE;
  if (!gDeviceDiscoverDriverConfig.ThreadedDeviceStart || (SubThreadsRunning == 0)) {
    Status = DeviceDiscoveryNotify (
               DeviceDiscoveryEnumerationCompleted,
               mImageHandle,
               NULL,
               NULL
               );
  }

  return Status;
}

EFI_STATUS
DeviceDiscoveryDriverInitialize (
  IN EFI_HANDLE        ImageHandle,
  IN EFI_SYSTEM_TABLE  *SystemTable
  )
{
  EFI_STATUS                    Status;
  VOID                          *Hob;
  TEGRA_PLATFORM_RESOURCE_INFO  *PlatformResourceInfo;

  Hob = GetFirstGuidHob (&gNVIDIAPlatformResourceDataGuid);
  if ((Hob != NULL) &&
      (GET_GUID_HOB_DATA_SIZE (Hob) == sizeof (TEGRA_PLATFORM_RESOURCE_INFO)))
  {
    PlatformResourceInfo = (TEGRA_PLATFORM_RESOURCE_INFO *)GET_GUID_HOB_DATA (Hob);
  } else {
    DEBUG ((DEBUG_ERROR, "Failed to get PlatformResourceInfo\n"));
    return EFI_NOT_FOUND;
  }

  if (PlatformResourceInfo->BootType == TegrablBootRcm) {
    // If doing RCM mode, check if driver is required in RCM mode.
    if (gDeviceDiscoverDriverConfig.DisableInRcm) {
      return EFI_UNSUPPORTED;
    }
  }

  mImageHandle = ImageHandle;

  Status = gBS->LocateProtocol (&gArmScmiClock2ProtocolGuid, NULL, (VOID **)&gScmiClockProtocol);
  if (EFI_ERROR (Status)) {
    return Status;
  }

  Status = gBS->LocateProtocol (&gNVIDIAClockParentsProtocolGuid, NULL, (VOID **)&gClockParentsProtocol);
  if (EFI_ERROR (Status)) {
    return Status;
  }

  Status = DeviceDiscoveryNotify (
             DeviceDiscoveryDriverStart,
             mImageHandle,
             NULL,
             NULL
             );
  if (EFI_ERROR (Status)) {
    return Status;
  }

  if (!gDeviceDiscoverDriverConfig.DelayEnumeration) {
    Status = DeviceDiscoveryEnumerateDevices ();
    ASSERT_EFI_ERROR (Status);
  }

  return Status;
}
