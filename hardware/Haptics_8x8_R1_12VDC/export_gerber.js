// Run in EasyEDA Pro's Advanced > Run Script with this PCB selected.
// This release has no standalone copper fill/region. Its XT30 model marker
// otherwise leaks into top copper when SolidRegion is exported.
(async () => {
    const source = await eda.pcb_Document.getDocumentSource();
    let isPcb = true;
    for (const line of source.split('\n').filter(Boolean)) {
        const [header, payload] = line.split('||');
        const h = JSON.parse(header);
        const body = payload.replace(/\|+$/, '');
        const value = body ? JSON.parse(body) : null;
        if (h.type === 'DOCHEAD') isPcb = value.docType === 'PCB';
        if (isPcb && value && ['FILL', 'POLY', 'REGION'].includes(h.type)
                && [1, 2, 12, 15, 16].includes(value.layerId)) {
            throw new Error('Copper region added: review the SolidRegion export setting before exporting.');
        }
    }
    const objects = ['Pad', 'Via', 'Track', 'Text', 'Image', 'Dimension',
        'BoardOutline', 'BoardCutout', 'CopperFilled', 'FPCStiffener', 'Line',
        'PlaneZone', 'ComponentProperty', 'ComponentSilkscreen', 'TearDrop'];
    const file = await eda.pcb_ManufactureData.getGerberFile(
        'Haptics_8x8_R1_release', false, 'mm',
        {integerNumber: 4, decimalNumber: 6}, undefined, undefined, objects);
    if (!file) throw new Error('Gerber export returned no file.');
    await eda.sys_FileSystem.saveFile(file, 'Haptics_8x8_R1_release_Gerber.zip');
})();
