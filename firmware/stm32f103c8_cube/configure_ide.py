"""Restore application build options after CubeMX regenerates IDE metadata."""
import argparse
import copy
from pathlib import Path
import xml.etree.ElementTree as ET
import zlib

ROOT = Path(__file__).resolve().parent
NAME = "haptics_f103c8"
BASE = "com.st.stm32cube.ide.mcu.gnu.managedbuild.tool.c."


def configure_profiles(root):
    """Keep business and diagnosis in separate CDT build directories."""
    settings = root.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']")
    debug = next(c for c in settings.findall("cconfiguration")
                 if c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name") == "Debug")
    for mode, description in (("ChannelTest", "Sequential single-channel diagnostic firmware"),
                              ("PinTest", "Fixed-pin diagnostic firmware; parameters in pin_test_config.h")):
        channel = next((c for c in settings.findall("cconfiguration")
                        if c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name") == mode), None)
        if channel is not None:
            continue
        channel = copy.deepcopy(debug)
        ids = {}
        for element in channel.iter():
            old = element.get("id")
            if old and element.tag != "extension":
                base = old.rstrip(".").rsplit(".", 1)[0]
                ids[old] = base+"."+str(zlib.crc32((old+mode).encode("utf-8")))
        for element in channel.iter():
            for key, value in list(element.attrib.items()):
                # Replace references as well as IDs, including folderInfo's final dot.
                for old in sorted(ids, key=len, reverse=True):
                    if value == old:
                        value = ids[old]
                        break
                element.set(key, value)
        channel.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").set("name", mode)
        channel.find("storageModule[@moduleId='cdtBuildSystem']/configuration").set("name", mode)
        channel.find("storageModule[@moduleId='cdtBuildSystem']/configuration").set(
            "description", description)
        for builder in channel.iter("builder"):
            builder.set("buildPath", "${workspace_loc:/"+NAME+"}/"+mode)
        settings.append(channel)
    for config in settings.findall("cconfiguration"):
        mode = config.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name")
        for option in config.iter("option"):
            if option.get("superClass") == BASE+"compiler.option.definedsymbols":
                for name, enabled in (("F103_CHANNEL_TEST", mode in ("ChannelTest", "PinTest")),
                                      ("F103_PIN_TEST", mode == "PinTest")):
                    matches = [v for v in option if v.get("value", "").split("=",1)[0] == name]
                    item = matches[0] if matches else ET.SubElement(option,"listOptionValue")
                    item.set("builtIn","false")
                    item.set("value",name+"="+str(int(enabled)))
                    for duplicate in matches[1:]:
                        option.remove(duplicate)
        for chain in config.iter("toolChain"):
            for option in chain.findall("option"):
                if option.get("superClass") == "com.st.stm32cube.ide.mcu.gnu.managedbuild.option.runtimelibrary_c":
                    option.set("id", option.get("superClass")+"."+chain.get("id").rsplit(".", 1)[-1])


def configure_run(profile="Debug"):
    """Restore the green Run button after CubeMX creates its ST-only launch."""
    configuration = next(c for c in ET.parse(ROOT / ".cproject").getroot().iter("cconfiguration")
                         if c.find("storageModule[@moduleId='org.eclipse.cdt.core.settings']").get("name") == profile)
    local_config = {"Debug": "haptics_run.local.cfg", "ChannelTest": "haptics_channel_test.local.cfg",
                    "PinTest": "haptics_pin_test.local.cfg"}[profile]
    launch = ET.Element("launchConfiguration", type="org.eclipse.cdt.launch.applicationLaunchType")
    attributes = (
        ("string", "org.eclipse.cdt.launch.PROGRAM_NAME", "${stm32cubeide_openocd_path}/openocd.exe"),
        ("string", "org.eclipse.cdt.launch.PROGRAM_ARGUMENTS", "-f "+local_config),
        ("string", "org.eclipse.cdt.launch.PROJECT_ATTR", NAME),
        ("string", "org.eclipse.cdt.launch.WORKING_DIRECTORY", "${workspace_loc:/"+NAME+"}"),
        ("int", "org.eclipse.cdt.launch.ATTR_BUILD_BEFORE_LAUNCH_ATTR", "1"),
        ("boolean", "org.eclipse.cdt.launch.PROJECT_BUILD_CONFIG_AUTO_ATTR", "false"),
        ("string", "org.eclipse.cdt.launch.PROJECT_BUILD_CONFIG_ID_ATTR", configuration.get("id")),
    )
    for kind, key, value in attributes:
        ET.SubElement(launch, kind+"Attribute", key=key, value=value)
    for key, value in (
        ("org.eclipse.debug.core.MAPPED_RESOURCE_PATHS", "/"+NAME),
        ("org.eclipse.debug.core.MAPPED_RESOURCE_TYPES", "4"),
        ("org.eclipse.debug.ui.favoriteGroups", "org.eclipse.debug.ui.launchGroup.run"),
    ):
        field = ET.SubElement(launch, "listAttribute", key=key)
        ET.SubElement(field, "listEntry", value=value)
    ET.indent(launch)
    launch_name = {"Debug": NAME, "ChannelTest": NAME+" Channel Test", "PinTest": NAME+" Pin Test"}[profile]
    (ROOT / (launch_name+".launch")).write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        + ET.tostring(launch, encoding="unicode")+"\n", encoding="utf-8")


def configure_debug(ide_root):
    plugins = ide_root / "plugins"
    if not plugins.is_dir():
        plugins = ide_root / "STM32CubeIDE" / "plugins"
    binaries = sorted(plugins.glob("com.st.stm32cube.ide.mcu.externaltools.openocd.win32_*/tools/bin/openocd.exe"))
    scripts = sorted(plugins.glob("com.st.stm32cube.ide.mcu.debug.openocd_*/resources/openocd/st_scripts"))
    objcopies = sorted(plugins.glob("com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.*/tools/bin/arm-none-eabi-objcopy.exe"))
    if not binaries or not scripts or not objcopies:
        raise SystemExit("CubeIDE OpenOCD/GNU tools were not found in " + str(ide_root))
    command = (f'target remote | "{binaries[-1].resolve().as_posix()}" '
               f'-s "{scripts[-1].resolve().as_posix()}" '
               f'-f "{(ROOT / (NAME + ".cfg")).as_posix()}" '
               '-c "gdb_port pipe" -c "tcl_port disabled" -c "telnet_port disabled"')
    (ROOT / "haptics_openocd.local.gdb").write_text(
        "set remotetimeout 10\n" + command + "\nmonitor reset halt\n"
        "maintenance flush register-cache\n", encoding="utf-8")
    # OpenOCD runs as a local tool from the ordinary green Run button.
    # Keep machine paths outside the shared launch configuration.
    def tcl_path(path):
        value = path.resolve().as_posix()
        if any(char in value for char in "{}\n\r"):
            raise SystemExit("Unsupported character in tool/project path: " + value)
        return "{" + value + "}"

    for profile, filename in (("Debug", "haptics_run.local.cfg"),
                              ("ChannelTest", "haptics_channel_test.local.cfg"),
                              ("PinTest", "haptics_pin_test.local.cfg")):
        (ROOT / filename).write_text(
            f"add_script_search_dir {tcl_path(scripts[-1])}\n"
            f"source {tcl_path(ROOT / (NAME + '.cfg'))}\n"
            "gdb_port disabled\ntcl_port disabled\ntelnet_port disabled\n"
            f"set elf {tcl_path(ROOT / profile / (NAME + '.elf'))}\n"
            f"set image {tcl_path(ROOT / profile / (NAME + '.flash.bin'))}\n"
            f"if {{![file exists $elf]}} {{ error {{Build the {profile} configuration first.}} }}\n"
            f"exec {tcl_path(objcopies[-1])} -O binary --gap-fill 0xff $elf $image\n"
            "if {[file size $image] <= 0 || [file size $image] > 0xfc00} {\n"
            "    error {Firmware exceeds the 63 KB application area; boot journal preserved.}\n"
            "}\n"
            "program $image verify reset exit 0x08000000\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cubeide", type=Path, help="CubeIDE installation root; also configure OpenOCD Run/Debug tools")
    args = parser.parse_args()
    for name in (".project", ".cproject"):
        path = ROOT / name
        tree = ET.parse(path)
        root = tree.getroot()
        for element in root.iter():
            for key, value in list(element.attrib.items()):
                element.set(key, value.replace("C8T6car", NAME))
        if name == ".project":
            root.find("name").text = NAME
        else:
            for chain in root.iter("toolChain"):
                key = "com.st.stm32cube.ide.mcu.gnu.managedbuild.option.runtimelibrary_c"
                option = next((o for o in chain.findall("option") if o.get("superClass") == key), None)
                if option is None:
                    option = ET.SubElement(chain, "option", {"superClass": key})
                option.set("id", key+"."+chain.get("id").rsplit(".", 1)[-1])
                option.set("valueType", "enumerated")
                option.set("value", key+".value.nano_c")
            configure_profiles(root)
            for option in root.iter("option"):
                super_class = option.get("superClass", "")
                if super_class == BASE+"compiler.option.optimization.level":
                    option.set("value", super_class+".value.o2")
                    option.set("valueType", "enumerated")
                if super_class == BASE+"linker.option.script":
                    option.set("value", "${workspace_loc:/${ProjName}/haptics_memory.ld}")
                if super_class.endswith("debug.option.cpuclock"):
                    option.set("value", "64")
            for tool in root.iter("tool"):
                if tool.get("superClass") == BASE+"linker":
                    for option in list(tool.findall("option")):
                        if option.get("superClass") == BASE+"linker.option.libs":
                            tool.remove(option)
            for entries in root.iter("sourceEntries"):
                for entry in list(entries):
                    entries.remove(entry)
                for source in ("Core", "Drivers/STM32F1xx_HAL_Driver/Src"):
                    ET.SubElement(entries,"entry",{"flags":"VALUE_WORKSPACE_PATH|RESOLVED",
                                                  "kind":"sourcePath","name":source})
        ET.indent(tree)
        prolog = '<?xml version="1.0" encoding="UTF-8"?>\n'
        if name == ".cproject":
            prolog += '<?fileVersion 4.0.0?>\n'
        path.write_text(prolog+ET.tostring(root,encoding="unicode"),encoding="utf-8")
    configure_run()
    configure_run("ChannelTest")
    configure_run("PinTest")
    if args.cubeide:
        configure_debug(args.cubeide)


if __name__ == "__main__":
    main()
