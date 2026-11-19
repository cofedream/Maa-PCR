"""Pinned release identity and the exact public manifest accepted for each patch."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
RELEASE_REVISION = 89
RELEASE_VERSION = 'v0.2.1'

PATCH_BASES = {
    39: (38, '59fa329ba5ac76de8e51ee10765573a7be737b81228137aa2aaf0cac55726668'),
    41: (39, 'b009f708728937026ef66828aef4e7c5d924f4de248f7eaaf7792dd168cdd6bd'),
    45: (41, '792ec2058f6ecb196832c996de9f188b159f0ecc33673a1f83054bda77f7cbd4'),
    46: (45, '76419fcf5772b6ff936c556bfd50200002b9b85b0c34717ba7e61ec2453034fb'),
    49: (46, '4b3d3ab862c609e076b8386303141e70c91d303b4b18d6169619a318b90dfd7b'),
    50: (49, 'de76ebf890e75cb15f345ee7b022de5b25b14f3c630b0e5ad374ca58c65d641e'),
    51: (50, '3314215f352678c082cf72c21b5e32d449396b2415b41ab7e27f4bf93b5a756f'),
    52: (51, 'a5fc27677b22e43133266a041fa75b8e85f7d30bd20f26497abaea5e00e62885'),
    53: (52, 'a1aa22db44eb19f5a2e8279c337f1b20b8097d93b4f8ef666be4cec5492bd85e'),
    55: (53, 'c27d3e9f144aa0af1c06bcaa58374012c55b275024f68e800cb2c9623b776776'),
    56: (55, '92798604770de2332c56b953486f969bd87f8a03a01d4595e5fbaa719548ed78'),
    57: (56, '1021513015b057b14da36898aa7ea9b1e7e5f8fef174617c5d0de6d578951575'),
    58: (57, 'aeafa623f25910427b08dbe27f78718a2d31c3670e3e4271497feea2c633c07b'),
    59: (58, '8eee3c459ec4405dfb583ded58b8561dd4497771ac49f3ce52f9ba1b04cd1a32'),
    60: (59, '6b20732e5e9a988f290696b435688bb4dd26ed1c10c69138f054deb8a08ad4ed'),
    61: (60, 'bbda52a6990393ccd41a86148682e15e2f548bb9f109bb270c7417ec0c25e3fe'),
    64: (61, 'ea4396bff4162622af42008bb8c109addc06b30663fadac455c52fbac8589a4a'),
    65: (64, '05cb9d27d89f860abaa8965003602eee574aa15e8c454bae3d01dbc68a0298ad'),
    66: (65, '64cee56fb55858feac9daa73997ccf2eac66d6a1ad251065f040a3a146c0d698'),
    67: (66, '68f18726b9828d6b1040b067b8b23325c6553d1a198c5466d2a2631d82d9c58b'),
    68: (67, '61c70d299c5ef0242a716543474e9f227b91a6e2d19fddd1e6c77f32f6de3b1c'),
    69: (68, '20079bc05a78e59cee4c6dacf03f926c35a93de9233c7ddd25c116f02ca5ff5f'),
    70: (69, '1aae6c870c950a8af08e31b5587e8f503d6445d837a481d3b304ac3749c054af'),
    71: (70, '0874649a122c34dea2c6715b9667a332289b8ae6e1bd8bb0659bcf80cf834fd8'),
    72: (71, 'ae7ae607f6e483ab85f38eeba048603a152da7d73dae487099e441056d23453c'),
    73: (72, '96e933259ffe70c18888b777d2d63c7e7934608daff868c7deac5367c59da5c1'),
    74: (73, 'b94de28b0de627499077a4c47e16428cae79d79ab07913f10ccbf9a9998c8a05'),
    75: (74, 'cb6c6182bd70f207e8afe3aa45eda2d0d8596679d7a1efeab09c0fb653eb10ab'),
    76: (75, '04adf05df7b9dd91de533dcd00827477c60eea2b3f129a52847c90df16f4afb7'),
    77: (76, '16610f8aa288551b30f2e764ed1aa3b6a96a643d329b4247c02f2b11d940ebd4'),
    78: (77, '378b275b25bc24727cd84256e80bcbaec4f43d4009e6f4add976b31d89e557cb'),
    79: (78, 'b97e57784aa58d212b9eb567815f44c65a9dfde6a8b172bf15f0654e6be04a1a'),
    80: (79, 'd61b28aca9270338af32b23b14e931c1d2aba6e072e23287471949c7352cbaf5'),
    81: (80, 'd9f4a851247fb61f82eaf33a65cb707a948214bfc6d9e0e1251fe36bac153c8e'),
    82: (81, '94dad3257a791e07c1ce03933e2997397d5e1f54dd4ded2bf99adc4d09229ce1'),
    83: (82, '35aa5cd9beca352f61bedfb0b8a0f95765262c2e77f58b2a00d65a069f53903c'),
    84: (83, 'c99d8df0cc52b31722107e823a8782c1da0db76cfa2f1346e14e098526094389'),
    85: (84, '1a245bd395e1bde63a8f761ca56c7ece0628f7bc479b491902ee078eddac11df'),
    86: (85, '8c548b5bb627047c66a626ea7d8ba330bbb295385d6b5b16b96c7976184240a2'),
    87: (86, 'eb282da9d57d5e8b9d9697486111fbe08f37472011c6ab7c7ebd55ab28829a54'),
    88: (87, 'f3851fbcc0dc9d359c7648e5f71e00d2baeaea80132b657af893938da47f5df5'),
    89: (88, '3054967069d5bbe7cc2c08f89cf5ffaf3b16ff7034aa060a982e151098bfcf04'),
}
BASELINE_HASHES = {base: checksum for base, checksum in PATCH_BASES.values()}


def baseline_filename(revision):
    return f'delivery/baselines/r{revision}-package-manifest.json'


def require_patch_base(revision, manifest):
    previous = PATCH_BASES.get(revision)
    if previous is None:
        return
    base, checksum = previous
    if manifest is None or not manifest.is_file():
        raise ValueError(f'R{revision}_requires_exact_R{base}_patch_base')
    with manifest.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != checksum:
            raise ValueError(f'R{revision}_requires_exact_R{base}_patch_base')


def check_release_baseline(*, revision=RELEASE_REVISION, version=RELEASE_VERSION, root=ROOT):
    if (revision, version) != (RELEASE_REVISION, RELEASE_VERSION):
        raise ValueError('release_identity_mismatch')
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    if 'v' + project['project']['version'] != version:
        raise ValueError('release_project_version_mismatch')
    base, _ = PATCH_BASES[revision]
    manifest = root / baseline_filename(base)
    require_patch_base(revision, manifest)
    if json.loads(manifest.read_bytes())['update']['revision'] != base:
        raise ValueError('release_baseline_revision_mismatch')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default=RELEASE_VERSION)
    print(check_release_baseline(version=parser.parse_args().tag))
