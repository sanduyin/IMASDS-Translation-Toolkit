# src/ndstool/dsi_builder.py
"""
DSi (TWL) 扩展：DsiExtraFields 解析/生成、digest (HMAC-SHA1)、
modcrypt (AES-128 CTR)、ARM9 secure area 自定义块密码加解密，以及从
已重建 NTR 区生成完整可启动 TWL 区。

参考实现：
    dearlystars_tool (Rust) ndstool/header.rs       (DsiExtraFields 结构)
    dearlystars_tool (Rust) ndstool/digest.rs        (HMAC-SHA1)
    dearlystars_tool (Rust) ndstool/modcrypt.rs      (AES-CTR)
    dearlystars_tool (Rust) ndstool/key_encryption.rs (ARM9 secure area)

依赖：pycryptodome (AES-128 ECB)
"""
from __future__ import annotations

import struct
import hmac
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from Crypto.Cipher import AES
except ImportError:  # 只解析 Header 时不应被可选 AES 依赖阻断
    AES = None  # type: ignore[assignment]

from .crc import crc16_header
from .header import HEADER_SIZE as NDS_HEADER_SIZE, NDSHeader

# ======================================================================
# 常量
# ======================================================================

DSI_EXTRA_FIELDS_SIZE: int = 0xE80
"""DsiExtraFields 长度；位于 ROM 绝对偏移 0x180~0xFFF。"""

DSI_EXTRA_FIELDS_OFFSET: int = 0x180
TWL_HEADER_SIZE: int = 0x1000
ROM_HEADER_SIZE: int = 0x4000
SECTOR_ALIGNMENT: int = 0x400
BLOCK_SECTORCOUNT: int = 0x20
FILE_ALIGNMENT: int = 0x200
DSI_ALIGNMENT: int = 0x100000

MASK32: int = 0xFFFFFFFF
"""u32 位掩码，用于模拟 Rust u32 wrapping 算术。"""

MASK128: int = (1 << 128) - 1
"""u128 位掩码。"""

# ---- HMAC-SHA1 固定密钥 (digest.rs) ----

HMAC_SHA1_KEY: bytes = bytes([
    0x21, 0x06, 0xC0, 0xDE, 0xBA, 0x98, 0xCE, 0x3F,
    0xA6, 0x92, 0xE3, 0x9D, 0x46, 0xF2, 0xED, 0x01,
    0x76, 0xE3, 0xCC, 0x08, 0x56, 0x23, 0x63, 0xFA,
    0xCA, 0xD4, 0xEC, 0xDF, 0x9A, 0x62, 0x78, 0x34,
    0x8F, 0x6D, 0x63, 0x3C, 0xFE, 0x22, 0xCA, 0x92,
    0x20, 0x88, 0x97, 0x23, 0xD2, 0xCF, 0xAE, 0xC2,
    0x32, 0x67, 0x8D, 0xFE, 0xCA, 0x83, 0x64, 0x98,
    0xAC, 0xFD, 0x3E, 0x37, 0x87, 0x46, 0x58, 0x24,
])
"""DSi digest HMAC-SHA1 固定密钥 (64 字节)。"""

HASH_SIZE: int = 20
"""SHA1 HMAC 输出长度。"""

# ---- ARM9 secure area 块密码常量 (key_encryption.rs) ----

MAGIC_30: int = 0x72636E65  # "encr" LE
MAGIC_34: int = 0x6A624F79  # "yObj" LE

SECURE_AREA_SIZE: int = 0x4000
"""ARM9 secure area 长度。"""

SECURE_AREA_MAGIC: int = 0xE7FFDEFF
"""secure area 解密态起始 u32 magic（连续 2 个）。"""

# ENCR_DATA 查找表 (0x1048 字节 = 0x412 个 u32)
# 从 dearlystars_tool (Rust) ndstool/key_encryption.rs 提取
_ENCR_DATA_HEX = (
    "99d5205f5744f5b96e19a4d99e6a5a94d8aef1eb4175e23a9382d03233ee31d5"
    "cc57619a3706a21b793972f555aef6be5f1b69fbe59df1e9ce2cd9a15e3205e6"
    "fed3fecfd462040d8bf5ecb72b6079bb1295310d6e3fda2b8884f0f13d127e25"
    "4522f1bb24061a0611addf288b6481342beb332999aaf2bd9c14959d9ff7f58c"
    "7297a1299dd15fcf664d071aded34a4b85c9a7a31795053a3d490abf0a898ba2"
    "4a8249dd2790f10be9eb1c6a83764505ba817061173f4bdeaecfab3957f23a56"
    "4811ad8a40e1453ffa9b0254caa693fbef4dfe6fa3d8879c08bad5486a8d2dfd"
    "6e15f874bdbe528b18228a9efb7437071b366c4a19ba4262b97991107b676596"
    "fe0223e8ee998c773e5c86644d6d7886a54f65e21eb2df5a0ad07e0814b071ac"
    "bddb831cb9d7a162cdc6637c5269c3e6bf75ce12445d2104fafbd33c381163d4"
    "958541494609f2084311dc1f76c0156d1f3c6370ea87806cc3bd638bc2372137"
    "dcee09232e376a4d7390f75030ac1c92041023914fd207aa683e4f9ac964606a"
    "c81421f3d62241124424cfe68a56dd0d534de1851e8c525a9c1984c20357f16f"
    "e300be58f64cedd521649c1fbe55033c4adcffaac9dae05d5ebfe6def5d8b1f8"
    "ff36b3b9626795db315f37ed4c70679990b518316c3d9999e442dad3254213a0"
    "aed7706cb155cfc7d746d54361173d4428e93385d5d0a293aa25121ffbc50b46"
    "f597765645a6be87b1946be8b1fe3399ae1f3e6c39711d09009037e4103e7574"
    "ff8c833bb0f1b0f90105474295f1d6ac7e38e69e9574263fb4685018d04330b4"
    "4c4be368bfe54db6958b0aa074253277cfa1f72cd871135aabeac951e80deeef"
    "e9937e19a71e433881162ca148e373cc29216cd35dcea0d9617143a01513b564"
    "92cf2a19dcadb7a59f8665f81a9fe7fbf7fdb8136c27db6fdf351cf78d2c5b9b"
    "12ab386406ccde31e84e751164e3faeaeb3454c2ad3f34eb932c7d26369d56f3"
    "5ae1f6b398634a9e3283e49a84607d902e130eee934b36a285ec1638e8880602"
    "bff0a03aedd76a9a73e157cff844b8dc2e2359d1df9552719961a04bd57f6e78"
    "baa9c530d34086329d320c9c37b7022fba5498a9c41304c98dbec8e75d97502e"
    "93d622590c27bc2292e0a7200f936f7f4c9fd3b5a62a0b7467497d1026cbd1c5"
    "8671e78ca09ce95bb21af601ee8c9e5e83f21adbe6e5ea845976d27cf68da549"
    "3648c21652bb83a374b9070c3bff6128e161e9e4ef6e15aa4ebae85d0596bb32"
    "56b0fb72520f0ec84225657689aff2de1027f0014b74a79707d5265454091f82"
    "0a867d30390eb3269b0b57bb360631affd79fcd930102b0cb3e19bd77bdc5fef"
    "d2f813454d4775bd46963c7e75f33eb567c59a3bb05b296bde805bc81505b131"
    "b6ce49ddad84b5ae60dc67313430fe4ebd802fa6bf63392186d9357f16682205"
    "54e990268c076c51a43155d70907a83e2e5366c1f8f27bc4f258cff187c5a2e7"
    "278f308758a064622318b9887cfacec498aead17cc4a5bf3e948d556d30df2c8"
    "92738cdbd72f56ac81f992694dc632f6e6c08d21e276806111bcdc6c93af1969"
    "9bd0bfb9319f0267a351ee8306227b0cab494240b8d5017dce5ef7555339c599"
    "46d8879fbaf764b4e39afaa16d90681030ca8a54a79f60c319f56b0d7a5198e6"
    "984351b4d635e94fc3df0f7bd62f5cbd3a156119f14bcbaadc6d64c9d3c61e56"
    "ef384c50718675cc0d0d4ee928f6065d701baad345cfa839ac95a62eb4e422d4"
    "74a8375f487a04cca54c40d828b428080d1c725241f07d47193a534e5884626b"
    "93b58a81214e0ddcb43fa2c6fcc92b40da3804e95e5a866b0c22258568118d7c"
    "921d95554dab8ebbdaa6e6b751b6325a0541dd052a0a5650911747ccc9e67eb5"
    "614adb736751c833f5da6e742e54c3370d6daf08e8158a5fe25921cda8de0c06"
    "5a776b5fdb18653ec850de78e0b882b35d4e7232074fc13423ba96b7674ea428"
    "1e3462eb2d6a70e92f42c4704e5a319cf95b4728aada716f381fb378c4926b1c"
    "9ef6359ab74d0ebfcc1829410348355d55d02bc629af5c6074698e5e9b7cd4bd"
    "7b44647d3f925d69b61f004bd48335cf7e644e17ae8dd52e9a28124e2e2b4908"
    "5caec64685ae41611e6f82d25137161f0bf659a49aca5aaf0dd4338b2063f184"
    "805ccbcf08b4b9d31605bd6283319b5651989fbab25baab2226b2cb5d448fa63"
    "2b5f58fa61fa6409bb38e0b89d9260a80d676f0e37f50d019fc277d4feecf173"
    "3039e07df56198e42c2855045655db2f6bece55806b664806a2a1a4e5b0fd8c4"
    "0a2e5219d962f53048be8c7b4f389ba2c3afc9d3c7c1624186b96121576f994f"
    "c1bace7bb53b4d5e8a8b44575f135f706d5b2947dc38e2ec045565122ae81743"
    "e18edd2ab3e294f7096e5ce6eb8af86d89495448f52fadbfea944bcafc398782"
    "5f8a01f275f2e671d6d842def12d1d28a6887ea3a0471d30d9a371df491ccb01"
    "f836b1f2f022585d456bbda0bbb28842c78c28ce93e8906308907c893cf57db7"
    "042d4f555116fd7e79e8bec1f212d4f8b4840523a0ccd22bfde1abad0dd1556c"
    "2341944d77374f05280cbf17b312676c8cc35af741842a6dd09412272cb4ed9c"
    "4dec478297d567b91b9dc055077ee58ee2a8e73e12e40e3a2a455534a2f92d5a"
    "1bab527c83105f55d2f15a432bc6a7a4891595e8b44b9df875e39f60785bd6e6"
    "0d44e62106bd472253a400ad8d43138539f7aafc38af7bedfce42b5450984cfc"
    "8580f7df3c8022e194dade24c6b07a3938dc0fa1a7f4f96f6318578b84412a2e"
    "d453f2d9000fd0dd996e19a60ad0ec5b5824abc0cb0665ec1a1338940a67032f"
    "3ff7e377447733c61439d0e3c0a20879bb409957410b0190cde1cc4867dbb3af"
    "8874f34c828f72b1b52329c4126c19fc8e46a49cc4256587d36dbe8a93110338"
    "ed832bf346a493ea3b53851dced4f1088327edfc9b1a18bcf98baedc24ab5038"
    "e9724b1022177b465dab5964f340aef8bbe5c8f926034e557debebfef739e6e0"
    "0a11be2e28ff98edc0c9425642c3fd00f6af87a25b013f329247959a72a5323d"
    "ae6bd09b07d24992e3784afaa1067df241cf77740414b20c86846416d5bb51a1"
    "e56ff1d1f2e2f75f58204db857c7cfddc5d8be763df65f7ee72a8b88241b383f"
    "0e412377f5f04bd40c1ffaa40b805fcf45f6e0da2f345953fb203c52625e35b5"
    "62fe8b6063e3865a151a6ed14745bc32b4eb6738abe46e333ab5eda3ad67e04e"
    "4195ee626271261d31ef6230afd782acc2dc0504f59707bf11592307c06402e8"
    "97e53eaf18ac59a68b4a33901c6e7c9c207e4c3c3e6164bbc56b7c7e3e9fc54c"
    "9fea73f5d789c04cf4fbf42dec141b51d5c112c810df0b4a8b9cbc93456a3e3e"
    "7dc1a9bacdc1b407e4e1688643b26d38f3fb0c5c663771de56ef6ea0104065a7"
    "98f7d0be0ec83736ec10ca7c9cab841e051776021c4f52aa5fc1c6a056b9d804"
    "84444da759d8de60e6380e058f03e13b6d8104336f300bce69052133fb26bb89"
    "7db6ae877e5107e0acf7960a6bf9c45c1de44447b85efae3788455424b485ef7"
    "7d4735861d2b430503ec8ab81e063c760c481a43a7b78aed1e13c643ee10efdb"
    "ecfb3c83b29544efd854514e2d11441dfb36591e7a34c1c3ca570061ea67a516"
    "9b55d055e17fd936d24076aedc01ceb07a83d5cb2098ec6bc1729234f3825737"
    "628a32360c9043aeae5c9b788e136502fd6871c1feb031a02482b0c3b17969a7"
    "f5d2ebd082c032dc9ec7263c6d8d98c1bb22d4d00f33ec3eb9cce1dc6a4c7736"
    "141cf9bf819f285f71853229907548c4b34aced8448f142ffd4057efaa0875d9"
    "46d1d66e32551fc318fe841ffc84d5ff715e1b48c386950e280827d33883717b"
    "4c8063549a56b0accf80ca3109effef3beaf247ea6fe533fc28d4a3368d122a6"
    "66ad7beadeb643b0a1259500a33f7546141144ecd795bc92f04fa91653629760"
    "2a0f41f17124beee947f08cd6093b3855b07003fd80f28839ad1699fd1da2ec3"
    "9001a2b96b4e2a669ddaaea6ea2ad3682f0c0c9cd28c4aede29e57659d0987a3"
    "b4c4325dc9d4322bb1e0711e644de69071e31e40ed7df3840eedc87876aec071"
    "2772bb05ea0264fbf3486bb542933fed9f1353d2f7fe2aec1d4725db3c9186c6"
    "8ef011fd237436f7a4f59e7a7e535044d447cad3eb386de6d971947f4ac6694b"
    "11f452ea22fe8ab036678b59e8e6802aeb650413eeecdc9e5fb1ec056a59e69f"
    "5e596b89bff71aca44f95b6a718503e42962e0706f41c4cfb2b1cce37ea607a8"
    "87e77f8493db524b6cec7eddd4244810699f046074e64818f3e42cb94f2e507a"
    "dfd454692b8ba7f3ceff1ff33e26013917958489b0f04c4b82919fc44bac9da5"
    "74af1725c9ca32d3bc898a8489cc0dae7ca2db9c6a7891eeea765d4e8760f569"
    "1567d402cfaf483607eabf6f662d068fc49afef9f6908775b8f7ad0f76105a3d"
    "59b02eb3c7352ccc70562bcbe33796c52f461b8a2246c788a726329861df8622"
    "8af41c2f87a109aacca9aed3bd00451c9a5487865287efff1e8fa18fc1895c35"
    "1bda2d3a2c16b2c2f156e278c16b6397c5568fc9327f2caaafa6a8ac20912288"
    "dee4608bf94b42251ae37f9c2c19893a7e05d436cc6958c2c1328b2f9085eb7a"
    "3950a5a12792c566b0204f587e5583432b45e29ce4d812902c168356167903b3"
    "ad2d61181a131f37e2e19c737b80d5fd2d5187fc7baad71f2c7a8eaff48dbbcd"
    "95117c720bee6fe2b9afde3783de8c8d620567b796c68d56b60dd762bad64636"
    "bd8ec8e6ea2a6c1014ff6b5bfa823c46b1304346518a7d9b923e83795b555db2"
    "6c5ece90628e5398c90d6de52d57cdc58157bae1e8b88f72e54f13dcea9d7115"
    "10b21188d509d47f5b657f2c3b384c1168508dfb9eb059bf9480894ac51a1812"
    "8953d14a1029e88c1cecb6ea46c7178b251531a8a26b43b19de2db0b879bb011"
    "040e71d2297789820a66417f1d0b48ff72bb24fdc248a19bfe7b7fce88db86d9"
    "853b1cb0dca83307bf512ee30e9a00971e06c097439dd8b645c486675f00f888"
    "9aa4529ec7aa8a8375ecc518aecec32f1a2bf918ffae1af5530bb53351a7fde8a"
    "8e1a264b622174380cc0ad8ae3bba40d7d9924a89df0410ee9b182b6a77698a68"
    "f4f9b9a221156ee61e3b0362309b60417e259b9e8fc5521008f8c269a1211188"
    "375e793566ff1042186eed97b66b1c4e36e56d7db4e4bf20b9e0053a69d5b8e3"
    "d5dce0b9ac533e07a457ad77ff4818762aac492a8e47756d9f676330358c3905"
    "39d56f643a5badca0bbb82529945b193363699af13204436d8024409399285ff"
    "4a4a9787a663d7c7b5b524ed0fb46f0c585214d9a67bd379bc3858a1bd3b8406"
    "d81a06fd6ba8ea4b69280437ad8299fb0e1b85bda85d73cddc58750abe636c48"
    "e74ce4302b0460b915d8da8681758f96d48d1c5d70857c1c677bd50867a6ce4b"
    "0a6670b7e563d45b8a82ea1067cae2f4ef17852f2a5f8a9782f86ad63410eaeb"
    "c95c3ce149f846ebdebdf6a992f1aaa6a018b03ad30f1ff36fff31454344d350"
    "9af7880996c1ce76ccf22c2cbaad82778f1884c0d2079c3690834e0ba54f433e"
    "04ab784fd6fb09012490da6f3c3a610d7f694aeb2b3002b4dbe084a9ecd735bf"
    "377d8558cea94ee480c7a8d3306748eb29af2f746ab4a73f0f3f92aff3caacaf"
    "4bd994c043ca810d2f48a1b027d5d2ef4b0585a3de4d93303cf0bb4a8f30274c"
    "ebe33e64ed9a2f3bf182f0baf4cf7f40cbb0e17fbcaa57d3c974f2fa430d22d0"
    "f4774e93d785701f99bfb6de35f130a75e71f06b012d7b64f033530a3988f36b"
    "3aa66b35d22f43cd02fdb5e9bc5baad8a4197e0e5d94819e6f77add60e749396"
    "e7c4185fadf519"
)

ENCR_DATA: bytes = bytes.fromhex(_ENCR_DATA_HEX)
"""ARM9 secure area 块密码查找表 (0x1048 字节)。"""

# modcrypt key 派生常量
_MODCRYPT_ADDEND: int = 0xFFFEFB4E295902582A680F5F1A4F3E79
_MODCRYPT_ROTATE: int = 42


# ======================================================================
# DsiExtraFields 数据类
# ======================================================================

@dataclass
class DsiExtraFields:
    """
    DSi 扩展头部字段（0xE80 字节，ROM 绝对偏移 0x180~0xFFF）。

    字段布局严格对齐 Rust 的 DsiExtraFields 结构体
    (dearlystars_tool (Rust) ndstool/header.rs)。
    """

    # 0x000 - MBK 设置
    global_mbk_setting: bytes = b"\x00" * 0x14        # 0x000  [u8; 0x14]
    arm9_mbk_setting: tuple[int, int, int] = (0, 0, 0)  # 0x014  [u32; 3]
    arm7_mbk_setting: tuple[int, int, int] = (0, 0, 0)  # 0x020  [u32; 3]
    mbk9_wramcnt_setting: int = 0                     # 0x02C  u32

    # 0x030 - 区域/访问控制
    region_flags: int = 0                             # 0x030  u32
    access_control: int = 0                           # 0x034  u32
    scfg_ext_mask: int = 0                            # 0x038  u32
    reserved_c: bytes = b"\x00" * 0x03                # 0x03C  [u8; 3]
    appflags: int = 0                                 # 0x03F  u8

    # 0x040 - ARM9i/ARM7i 二进制描述
    dsi9_rom_offset: int = 0                          # 0x040  u32
    reserved_d: int = 0                               # 0x044  u32
    dsi9_ram_address: int = 0                         # 0x048  u32
    dsi9_size: int = 0                                # 0x04C  u32
    dsi7_rom_offset: int = 0                          # 0x050  u32
    device_list_ram_address: int = 0                  # 0x054  u32
    dsi7_ram_address: int = 0                         # 0x058  u32
    dsi7_size: int = 0                                # 0x05C  u32

    # 0x060 - Digest 区域描述
    digest_ntr_start: int = 0                         # 0x060  u32
    digest_ntr_size: int = 0                          # 0x064  u32
    digest_twl_start: int = 0                         # 0x068  u32
    digest_twl_size: int = 0                          # 0x06C  u32
    sector_hashtable_start: int = 0                   # 0x070  u32
    sector_hashtable_size: int = 0                    # 0x074  u32
    block_hashtable_start: int = 0                    # 0x078  u32
    block_hashtable_size: int = 0                     # 0x07C  u32
    digest_sector_size: int = 0                       # 0x080  u32
    digest_block_sectorcount: int = 0                 # 0x084  u32

    # 0x088 - Banner/共享内存
    banner_size: int = 0                             # 0x088  u32
    shared_20000_size: int = 0                        # 0x08C  u8
    shared_20001_size: int = 0                        # 0x08D  u8
    eula_version: int = 0                            # 0x08E  u8
    use_ratings: int = 0                             # 0x08F  u8
    total_rom_size: int = 0                          # 0x090  u32
    shared_20002_size: int = 0                        # 0x094  u8
    shared_20003_size: int = 0                        # 0x095  u8
    shared_20004_size: int = 0                        # 0x096  u8
    shared_20005_size: int = 0                        # 0x097  u8
    arm9i_parameters_table_offset: int = 0            # 0x098  u32
    arm7i_parameters_table_offset: int = 0            # 0x09C  u32

    # 0x0A0 - modcrypt 区域
    modcrypt1_start: int = 0                          # 0x0A0  u32
    modcrypt1_size: int = 0                           # 0x0A4  u32
    modcrypt2_start: int = 0                          # 0x0A8  u32
    modcrypt2_size: int = 0                           # 0x0AC  u32

    # 0x0B0 - 标题 ID / 存档大小
    tid_low: int = 0                                  # 0x0B0  u32
    tid_high: int = 0                                 # 0x0B4  u32
    public_sav_size: int = 0                          # 0x0B8  u32
    private_sav_size: int = 0                         # 0x0BC  u32

    # 0x0C0 - 保留/分级
    reserved_e: bytes = b"\x00" * 0xB0                # 0x0C0  [u8; 0xB0]
    age_ratings: bytes = b"\x00" * 0x10               # 0x170  [u8; 0x10]

    # 0x180 - HMAC 签名 (每个 0x14 = 20 字节)
    hmac_arm9: bytes = b"\x00" * 0x14                 # 0x180
    hmac_arm7: bytes = b"\x00" * 0x14                 # 0x194
    hmac_digest_master: bytes = b"\x00" * 0x14        # 0x1A8
    hmac_icon_title: bytes = b"\x00" * 0x14           # 0x1BC
    hmac_arm9i: bytes = b"\x00" * 0x14                # 0x1D0
    hmac_arm7i: bytes = b"\x00" * 0x14                # 0x1E4
    crypto_reserved_a: bytes = b"\x00" * 0x14         # 0x1F8
    crypto_reserved_b: bytes = b"\x00" * 0x14         # 0x20C
    hmac_arm9_no_secure: bytes = b"\x00" * 0x14       # 0x220

    # 0x234 - 保留/调试/RSA
    crypto_reserved_c: bytes = b"\x00" * 0xA4C         # 0x234  [u8; 0xA4C]
    debug_args: bytes = b"\x00" * 0x180                # 0xC80  [u8; 0x180]
    rsa_signature: bytes = b"\x00" * 0x80              # 0xE00  [u8; 0x80]

    def __post_init__(self) -> None:
        """构造后校验所有字节数组字段的长度。"""
        self._validate_bytes("global_mbk_setting", 0x14)
        self._validate_bytes("reserved_c", 0x03)
        self._validate_bytes("reserved_e", 0xB0)
        self._validate_bytes("age_ratings", 0x10)
        for name in (
            "hmac_arm9", "hmac_arm7", "hmac_digest_master", "hmac_icon_title",
            "hmac_arm9i", "hmac_arm7i", "crypto_reserved_a", "crypto_reserved_b",
            "hmac_arm9_no_secure",
        ):
            self._validate_bytes(name, 0x14)
        self._validate_bytes("crypto_reserved_c", 0xA4C)
        self._validate_bytes("debug_args", 0x180)
        self._validate_bytes("rsa_signature", 0x80)

    def _validate_bytes(self, name: str, expected_len: int) -> None:
        v = getattr(self, name)
        if not isinstance(v, (bytes, bytearray)):
            raise TypeError(f"{name} 必须是 bytes/bytearray，实际为 {type(v).__name__}")
        if len(v) != expected_len:
            raise ValueError(f"{name} 长度错误：期望 {expected_len}，实际 {len(v)}")

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, data: bytes | bytearray) -> "DsiExtraFields":
        """解析 DsiExtraFields；``data[0]`` 必须对应 ROM 绝对偏移 0x180。"""
        if len(data) < DSI_EXTRA_FIELDS_SIZE:
            raise ValueError(
                f"DsiExtraFields 数据过短：{len(data)} < 0x{DSI_EXTRA_FIELDS_SIZE:X}"
            )
        d = bytes(data[:DSI_EXTRA_FIELDS_SIZE])

        def u32(off: int) -> int:
            return int(struct.unpack_from("<I", d, off)[0])

        def u8(off: int) -> int:
            return d[off]

        return cls(
            global_mbk_setting=d[0x000:0x014],
            arm9_mbk_setting=(u32(0x014), u32(0x018), u32(0x01C)),
            arm7_mbk_setting=(u32(0x020), u32(0x024), u32(0x028)),
            mbk9_wramcnt_setting=u32(0x02C),
            region_flags=u32(0x030),
            access_control=u32(0x034),
            scfg_ext_mask=u32(0x038),
            reserved_c=d[0x03C:0x03F],
            appflags=u8(0x03F),
            dsi9_rom_offset=u32(0x040),
            reserved_d=u32(0x044),
            dsi9_ram_address=u32(0x048),
            dsi9_size=u32(0x04C),
            dsi7_rom_offset=u32(0x050),
            device_list_ram_address=u32(0x054),
            dsi7_ram_address=u32(0x058),
            dsi7_size=u32(0x05C),
            digest_ntr_start=u32(0x060),
            digest_ntr_size=u32(0x064),
            digest_twl_start=u32(0x068),
            digest_twl_size=u32(0x06C),
            sector_hashtable_start=u32(0x070),
            sector_hashtable_size=u32(0x074),
            block_hashtable_start=u32(0x078),
            block_hashtable_size=u32(0x07C),
            digest_sector_size=u32(0x080),
            digest_block_sectorcount=u32(0x084),
            banner_size=u32(0x088),
            shared_20000_size=u8(0x08C),
            shared_20001_size=u8(0x08D),
            eula_version=u8(0x08E),
            use_ratings=u8(0x08F),
            total_rom_size=u32(0x090),
            shared_20002_size=u8(0x094),
            shared_20003_size=u8(0x095),
            shared_20004_size=u8(0x096),
            shared_20005_size=u8(0x097),
            arm9i_parameters_table_offset=u32(0x098),
            arm7i_parameters_table_offset=u32(0x09C),
            modcrypt1_start=u32(0x0A0),
            modcrypt1_size=u32(0x0A4),
            modcrypt2_start=u32(0x0A8),
            modcrypt2_size=u32(0x0AC),
            tid_low=u32(0x0B0),
            tid_high=u32(0x0B4),
            public_sav_size=u32(0x0B8),
            private_sav_size=u32(0x0BC),
            reserved_e=d[0x0C0:0x170],
            age_ratings=d[0x170:0x180],
            hmac_arm9=d[0x180:0x194],
            hmac_arm7=d[0x194:0x1A8],
            hmac_digest_master=d[0x1A8:0x1BC],
            hmac_icon_title=d[0x1BC:0x1D0],
            hmac_arm9i=d[0x1D0:0x1E4],
            hmac_arm7i=d[0x1E4:0x1F8],
            crypto_reserved_a=d[0x1F8:0x20C],
            crypto_reserved_b=d[0x20C:0x220],
            hmac_arm9_no_secure=d[0x220:0x234],
            crypto_reserved_c=d[0x234:0xC80],
            debug_args=d[0xC80:0xE00],
            rsa_signature=d[0xE00:0xE80],
        )

    def build(self) -> bytes:
        """序列化为 0xE80 字节 bytes。"""
        out = bytearray(DSI_EXTRA_FIELDS_SIZE)
        out[0x000:0x014] = self.global_mbk_setting
        struct.pack_into("<III", out, 0x014, *self.arm9_mbk_setting)
        struct.pack_into("<III", out, 0x020, *self.arm7_mbk_setting)
        struct.pack_into("<I", out, 0x02C, self.mbk9_wramcnt_setting & MASK32)
        struct.pack_into("<I", out, 0x030, self.region_flags & MASK32)
        struct.pack_into("<I", out, 0x034, self.access_control & MASK32)
        struct.pack_into("<I", out, 0x038, self.scfg_ext_mask & MASK32)
        out[0x03C:0x03F] = self.reserved_c
        out[0x03F] = self.appflags & 0xFF
        struct.pack_into("<I", out, 0x040, self.dsi9_rom_offset & MASK32)
        struct.pack_into("<I", out, 0x044, self.reserved_d & MASK32)
        struct.pack_into("<I", out, 0x048, self.dsi9_ram_address & MASK32)
        struct.pack_into("<I", out, 0x04C, self.dsi9_size & MASK32)
        struct.pack_into("<I", out, 0x050, self.dsi7_rom_offset & MASK32)
        struct.pack_into("<I", out, 0x054, self.device_list_ram_address & MASK32)
        struct.pack_into("<I", out, 0x058, self.dsi7_ram_address & MASK32)
        struct.pack_into("<I", out, 0x05C, self.dsi7_size & MASK32)
        struct.pack_into("<I", out, 0x060, self.digest_ntr_start & MASK32)
        struct.pack_into("<I", out, 0x064, self.digest_ntr_size & MASK32)
        struct.pack_into("<I", out, 0x068, self.digest_twl_start & MASK32)
        struct.pack_into("<I", out, 0x06C, self.digest_twl_size & MASK32)
        struct.pack_into("<I", out, 0x070, self.sector_hashtable_start & MASK32)
        struct.pack_into("<I", out, 0x074, self.sector_hashtable_size & MASK32)
        struct.pack_into("<I", out, 0x078, self.block_hashtable_start & MASK32)
        struct.pack_into("<I", out, 0x07C, self.block_hashtable_size & MASK32)
        struct.pack_into("<I", out, 0x080, self.digest_sector_size & MASK32)
        struct.pack_into("<I", out, 0x084, self.digest_block_sectorcount & MASK32)
        struct.pack_into("<I", out, 0x088, self.banner_size & MASK32)
        out[0x08C] = self.shared_20000_size & 0xFF
        out[0x08D] = self.shared_20001_size & 0xFF
        out[0x08E] = self.eula_version & 0xFF
        out[0x08F] = self.use_ratings & 0xFF
        struct.pack_into("<I", out, 0x090, self.total_rom_size & MASK32)
        out[0x094] = self.shared_20002_size & 0xFF
        out[0x095] = self.shared_20003_size & 0xFF
        out[0x096] = self.shared_20004_size & 0xFF
        out[0x097] = self.shared_20005_size & 0xFF
        struct.pack_into("<I", out, 0x098, self.arm9i_parameters_table_offset & MASK32)
        struct.pack_into("<I", out, 0x09C, self.arm7i_parameters_table_offset & MASK32)
        struct.pack_into("<I", out, 0x0A0, self.modcrypt1_start & MASK32)
        struct.pack_into("<I", out, 0x0A4, self.modcrypt1_size & MASK32)
        struct.pack_into("<I", out, 0x0A8, self.modcrypt2_start & MASK32)
        struct.pack_into("<I", out, 0x0AC, self.modcrypt2_size & MASK32)
        struct.pack_into("<I", out, 0x0B0, self.tid_low & MASK32)
        struct.pack_into("<I", out, 0x0B4, self.tid_high & MASK32)
        struct.pack_into("<I", out, 0x0B8, self.public_sav_size & MASK32)
        struct.pack_into("<I", out, 0x0BC, self.private_sav_size & MASK32)
        out[0x0C0:0x170] = self.reserved_e
        out[0x170:0x180] = self.age_ratings
        out[0x180:0x194] = self.hmac_arm9
        out[0x194:0x1A8] = self.hmac_arm7
        out[0x1A8:0x1BC] = self.hmac_digest_master
        out[0x1BC:0x1D0] = self.hmac_icon_title
        out[0x1D0:0x1E4] = self.hmac_arm9i
        out[0x1E4:0x1F8] = self.hmac_arm7i
        out[0x1F8:0x20C] = self.crypto_reserved_a
        out[0x20C:0x220] = self.crypto_reserved_b
        out[0x220:0x234] = self.hmac_arm9_no_secure
        out[0x234:0xC80] = self.crypto_reserved_c
        out[0xC80:0xE00] = self.debug_args
        out[0xE00:0xE80] = self.rsa_signature
        return bytes(out)


# ======================================================================
# HMAC-SHA1 Digest (digest.rs)
# ======================================================================

def sha1_hmac(data: bytes | bytearray, position: int, size: int) -> bytes:
    """
    计算 data[position:position+size] 的 HMAC-SHA1 (20 字节)。

    对应 Rust: sha1_hmac(reader, position, size)

    Args:
        data: 完整 ROM 数据
        position: 起始偏移
        size: 计算长度

    Returns:
        20 字节 HMAC-SHA1 摘要
    """
    if position < 0 or size < 0 or position > len(data) or size > len(data) - position:
        raise ValueError(
            f"HMAC 范围越界：offset=0x{position:X}, size=0x{size:X}, "
            f"data=0x{len(data):X}"
        )
    h = hmac.new(HMAC_SHA1_KEY, data[position : position + size], hashlib.sha1)
    return h.digest()


# ======================================================================
# AES-CTR Modcrypt (modcrypt.rs)
# ======================================================================

def get_key_ivs(
    gamecode: bytes,
    hmac_arm9: bytes,
    hmac_arm7: bytes,
    hmac_arm9i: bytes,
) -> tuple[int, int, int]:
    """
    派生 modcrypt 密钥与 IV。

    对应 Rust: get_key_ivs(header, dsi_header)

    Args:
        gamecode: 4 字节 gamecode
        hmac_arm9: 20 字节 HMAC (取前 16 字节作为 iv1)
        hmac_arm7: 20 字节 HMAC (取前 16 字节作为 iv2)
        hmac_arm9i: 20 字节 HMAC (取前 16 字节作为 key_y)

    Returns:
        (key, iv1, iv2) — 三个 128 位整数
    """
    # key_x = "Nintendo"(LE u64) | gamecode(LE u32)<<64 | gamecode(BE u32)<<96
    nintendo_le = int.from_bytes(b"Nintendo", "little")
    gamecode_le = int.from_bytes(gamecode, "little")
    gamecode_be = int.from_bytes(gamecode, "big")
    key_x = nintendo_le | (gamecode_le << 64) | (gamecode_be << 96)

    # key_y = hmac_arm9i[0..16] as LE u128
    key_y = int.from_bytes(hmac_arm9i[:16], "little")

    # key = (key_x ^ key_y).wrapping_add(0xFFFE...).rotate_left(42)
    key = ((key_x ^ key_y) + _MODCRYPT_ADDEND) & MASK128
    key = ((key << _MODCRYPT_ROTATE) | (key >> (128 - _MODCRYPT_ROTATE))) & MASK128

    # iv1 = hmac_arm9[0..16] as LE u128
    iv1 = int.from_bytes(hmac_arm9[:16], "little")
    # iv2 = hmac_arm7[0..16] as LE u128
    iv2 = int.from_bytes(hmac_arm7[:16], "little")

    return key, iv1, iv2


def aes_ctr(data: bytearray, key: int, iv: int) -> None:
    """
    AES-128 CTR 模式加解密 (就地修改)。

    对应 Rust: aes_ctr(data, key, iv)

    注意：Rust 实现使用大端序处理 counter/key block，但小端序处理数据 XOR。
    即：ciphertext[i] = AES_encrypt(counter)[15-i] ^ plaintext[i]
    这是 DSi 硬件的字节序约定。

    Args:
        data: 待加解密数据 (长度必须是 16 的倍数)，就地修改
        key: 128 位 AES 密钥
        iv: 128 位初始 counter
    """
    if len(data) % 16 != 0:
        raise ValueError("数据长度必须是 16 的倍数")
    if AES is None:
        raise RuntimeError(
            "缺少 pycryptodome；请先运行 `python -m pip install pycryptodome`"
        )

    blocks_count = len(data) // 16
    key_bytes = key.to_bytes(16, "big")

    cipher = AES.new(key_bytes, AES.MODE_ECB)

    # 生成所有 counter block (大端序)
    counters = b"".join(
        ((iv + i) & MASK128).to_bytes(16, "big") for i in range(blocks_count)
    )
    # 批量加密
    encrypted = cipher.encrypt(counters)

    # 对每个 16 字节块：C[i] = E(counter)[15-i] ^ P[i]
    # 即：反转加密块的字节序后与明文 XOR
    for i in range(blocks_count):
        off = i * 16
        # 反转加密 counter 块的字节序
        enc_block = encrypted[off : off + 16]
        for j in range(16):
            data[off + j] ^= enc_block[15 - j]


def modcrypt(
    rom_data: bytearray,
    gamecode: bytes,
    dsi_header: DsiExtraFields,
) -> None:
    """
    对 modcrypt1 / modcrypt2 区域执行 AES-CTR 加解密（就地修改）。

    modcrypt 是对称运算；对密文调用即解密，对明文调用即加密。faraplay
    0.5.2 的写入端只处理 modcrypt1，但读取端会处理两区；这里完整支持两区，
    以免未来 ARM7i 加密标题被静默破坏。

    Args:
        rom_data: 完整 ROM 数据 (就地修改)
        gamecode: 4 字节 gamecode
        dsi_header: DsiExtraFields 实例
    """
    if dsi_header.modcrypt1_size == 0 and dsi_header.modcrypt2_size == 0:
        return

    key, iv1, iv2 = get_key_ivs(
        gamecode,
        dsi_header.hmac_arm9,
        dsi_header.hmac_arm7,
        dsi_header.hmac_arm9i,
    )

    for name, start, size, iv in (
        ("modcrypt1", dsi_header.modcrypt1_start, dsi_header.modcrypt1_size, iv1),
        ("modcrypt2", dsi_header.modcrypt2_start, dsi_header.modcrypt2_size, iv2),
    ):
        if size == 0:
            continue
        if size % 16 != 0:
            raise ValueError(f"{name}_size ({size}) 不是 16 的倍数")
        if start < 0 or start > len(rom_data) or size > len(rom_data) - start:
            raise ValueError(
                f"{name} 越界：offset=0x{start:X}, size=0x{size:X}, "
                f"ROM=0x{len(rom_data):X}"
            )

        buffer = bytearray(rom_data[start : start + size])
        aes_ctr(buffer, key, iv)
        rom_data[start : start + size] = buffer


# ======================================================================
# ARM9 Secure Area 块密码 (key_encryption.rs)
# ======================================================================

def _init1(gamecode: int) -> tuple[list[int], list[int]]:
    """
    初始化 card_hash 查找表和 arg2。

    对应 Rust: init1(gamecode) -> ([u32; 0x412], [u32; 3])

    Returns:
        (card_hash, arg2) — card_hash 是 0x412 个 u32 的列表，arg2 是 3 个 u32
    """
    # card_hash = ENCR_DATA as u32[0x412] (little-endian)
    card_hash = list(struct.unpack_from(f"<{0x412}I", ENCR_DATA))
    arg2 = [
        gamecode & MASK32,
        (gamecode >> 1) & MASK32,
        (gamecode << 1) & MASK32,
    ]
    _init2(card_hash, arg2)
    _init2(card_hash, arg2)
    return card_hash, arg2


def _init2(magic: list[int], a: list[int]) -> None:
    """
    初始化辅助函数 (就地修改 magic 和 a)。

    对应 Rust: init2(magic, a)
    """
    # encrypt(magic, a2, a1) -> arg1=a[2], arg2=a[1]
    a[2], a[1] = _encrypt(magic, a[2], a[1])
    # encrypt(magic, a1, a0) -> arg1=a[1], arg2=a[0]
    a[1], a[0] = _encrypt(magic, a[1], a[0])
    # arg1 = [a0.to_le_bytes(), a1.to_le_bytes()].concat() -> 8 bytes
    arg1 = (a[0] & MASK32).to_bytes(4, "little") + (a[1] & MASK32).to_bytes(4, "little")
    _update_hashtable(magic, arg1)


def _encrypt(magic: list[int], arg1: int, arg2: int) -> tuple[int, int]:
    """
    块密码加密 (16 轮 Feistel)。

    对应 Rust: encrypt(magic, arg1, arg2)

    Returns:
        (new_arg1, new_arg2)
    """
    a = arg1 & MASK32
    b = arg2 & MASK32
    for i in range(16):
        c = (magic[i] ^ a) & MASK32
        a = (b ^ _lookup(magic, c)) & MASK32
        b = c
    new_arg2 = (a ^ magic[16]) & MASK32
    new_arg1 = (b ^ magic[17]) & MASK32
    return new_arg1, new_arg2


def _decrypt(magic: list[int], arg1: int, arg2: int) -> tuple[int, int]:
    """
    块密码解密 (逆 16 轮 Feistel)。

    对应 Rust: decrypt(magic, arg1, arg2)

    Returns:
        (new_arg1, new_arg2)
    """
    a = arg1 & MASK32
    b = arg2 & MASK32
    for i in range(17, 1, -1):  # 2..18 逆序 = 17, 16, ..., 2
        c = (magic[i] ^ a) & MASK32
        a = (b ^ _lookup(magic, c)) & MASK32
        b = c
    new_arg1 = (b ^ magic[0]) & MASK32
    new_arg2 = (a ^ magic[1]) & MASK32
    return new_arg1, new_arg2


def _lookup(magic: list[int], v: int) -> int:
    """
    4 字节查表：d + c ^ (b + a)。

    对应 Rust: lookup(magic, v)
    """
    v &= MASK32
    a = magic[((v >> 24) & 0xFF) + 18]
    b = magic[((v >> 16) & 0xFF) + 18 + 256]
    c = magic[((v >> 8) & 0xFF) + 18 + 512]
    d = magic[(v & 0xFF) + 18 + 768]
    return (d + (c ^ ((b + a) & MASK32))) & MASK32


def _update_hashtable(magic: list[int], arg1: bytes) -> None:
    """
    更新查找表 (就地修改 magic)。

    对应 Rust: update_hashtable(magic, arg1)
    arg1 是 8 字节数据。
    """
    # 第一阶段：XOR magic[0..18]
    for j in range(18):
        r3 = 0
        for i in range(4):
            r3 = (r3 << 8) | arg1[(j * 4 + i) & 7]
        magic[j] = (magic[j] ^ r3) & MASK32

    # 第二阶段：用 encrypt 生成 magic[0..18]
    tmp1 = 0
    tmp2 = 0
    for i in range(0, 18, 2):
        tmp1, tmp2 = _encrypt(magic, tmp1, tmp2)
        magic[i + 0] = tmp1
        magic[i + 1] = tmp2

    # 第三阶段：用 encrypt 生成 magic[18..0x412]
    for i in range(0, 0x400, 2):
        tmp1, tmp2 = _encrypt(magic, tmp1, tmp2)
        magic[i + 18 + 0] = tmp1
        magic[i + 18 + 1] = tmp2


def encrypt_arm9(gamecode: int, data: bytes | bytearray) -> bytes:
    """
    加密 ARM9 secure area (0x4000 字节)。

    对应 Rust: encrypt_arm9(gamecode, data)

    Args:
        gamecode: gamecode 的 u32 小端值
        data: 0x4000 字节解密态 secure area，必须以
              0xE7FFDEFF 0xE7FFDEFF 开头

    Returns:
        0x4000 字节加密态 secure area
    """
    if len(data) != SECURE_AREA_SIZE:
        raise ValueError(f"ARM9 secure area 必须是 0x{SECURE_AREA_SIZE:X} 字节")

    # 解析为 0x1000 个 u32 (小端)
    p = list(struct.unpack_from(f"<{0x1000}I", data))

    if p[0] != SECURE_AREA_MAGIC or p[1] != SECURE_AREA_MAGIC:
        raise ValueError(
            "待加密数据不以 FF DE FF E7 FF DE FF E7 开头！"
        )

    offset = 2
    card_hash, arg2 = _init1(gamecode)
    arg2[1] = (arg2[1] << 1) & MASK32
    arg2[2] = (arg2[2] >> 1) & MASK32
    _init2(card_hash, arg2)

    # 加密主体 (0x800-8 = 0x7F8 字节 = 0x1FE 个 u32 对)
    size = 0x800 - 8
    while size > 0:
        # encrypt(card_hash, p1=p[offset+1], p0=p[offset])
        p[offset + 1], p[offset] = _encrypt(card_hash, p[offset + 1], p[offset])
        offset += 2
        size -= 8

    # 放置 header magic 并双重加密
    p[0] = MAGIC_30
    p[1] = MAGIC_34
    p[1], p[0] = _encrypt(card_hash, p[1], p[0])
    card_hash = _init1(gamecode)[0]
    p[1], p[0] = _encrypt(card_hash, p[1], p[0])

    return struct.pack(f"<{0x1000}I", *p)


def decrypt_arm9(gamecode: int, data: bytes | bytearray) -> bytes:
    """
    解密 ARM9 secure area (0x4000 字节)。

    对应 Rust: decrypt_arm9(gamecode, data)

    Args:
        gamecode: gamecode 的 u32 小端值
        data: 0x4000 字节加密态 secure area

    Returns:
        0x4000 字节解密态 secure area (以 0xE7FFDEFF 0xE7FFDEFF 开头)
    """
    if len(data) != SECURE_AREA_SIZE:
        raise ValueError(f"ARM9 secure area 必须是 0x{SECURE_AREA_SIZE:X} 字节")

    p = list(struct.unpack_from(f"<{0x1000}I", data))

    card_hash, arg2 = _init1(gamecode)

    # 解密 header (双重加密的第一层)
    p[1], p[0] = _decrypt(card_hash, p[1], p[0])
    arg2[1] = (arg2[1] << 1) & MASK32
    arg2[2] = (arg2[2] >> 1) & MASK32
    _init2(card_hash, arg2)
    # 解密 header (第二层)
    p[1], p[0] = _decrypt(card_hash, p[1], p[0])

    if p[0] != MAGIC_30 or p[1] != MAGIC_34:
        raise ValueError(
            "解密后数据不以 65 6E 63 72 79 4F 62 6A 开头！"
        )

    # 恢复 magic
    p[0] = SECURE_AREA_MAGIC
    p[1] = SECURE_AREA_MAGIC

    # 解密主体
    size = 0x800 - 8
    offset = 2
    while size > 0:
        p[offset + 1], p[offset] = _decrypt(card_hash, p[offset + 1], p[offset])
        offset += 2
        size -= 8

    return struct.pack(f"<{0x1000}I", *p)


# ======================================================================
# 高级辅助函数
# ======================================================================

def encrypt_secure_area(
    rom_data: bytearray,
    gamecode: bytes,
) -> bool:
    """
    如果 ARM9 secure area 处于解密态，则加密它。

    对应 Rust: encrypt_secure_area(writer, header)

    Args:
        rom_data: 完整 ROM 数据 (就地修改)
        gamecode: 4 字节 gamecode

    Returns:
        True 如果执行了加密，False 如果 secure area 已是加密态
    """
    magic = struct.unpack_from("<Q", rom_data, 0x4000)[0]
    if magic != 0xE7FFDEFFE7FFDEFF:
        return False  # 已加密

    data_to_encrypt = bytes(rom_data[0x4000 : 0x4000 + SECURE_AREA_SIZE])
    gamecode_u32 = int.from_bytes(gamecode, "little")
    encrypted = encrypt_arm9(gamecode_u32, data_to_encrypt)
    rom_data[0x4000 : 0x4000 + SECURE_AREA_SIZE] = encrypted
    return True


def decrypt_secure_area(
    rom_data: bytearray,
    gamecode: bytes,
) -> bool:
    """
    解密 ARM9 secure area。

    对应 Rust: decrypt_secure_area(writer, header)

    Args:
        rom_data: 完整 ROM 数据 (就地修改)
        gamecode: 4 字节 gamecode

    Returns:
        True (总是执行解密)
    """
    data_to_decrypt = bytes(rom_data[0x4000 : 0x4000 + SECURE_AREA_SIZE])
    gamecode_u32 = int.from_bytes(gamecode, "little")
    decrypted = decrypt_arm9(gamecode_u32, data_to_decrypt)
    rom_data[0x4000 : 0x4000 + SECURE_AREA_SIZE] = decrypted
    return True


def write_digests(
    rom_data: bytearray,
    dsi_header: DsiExtraFields,
) -> None:
    """
    计算并写入 sector digests 和 block digests。

    对应 Rust: write_digests(writer, dsi_header)

    Args:
        rom_data: 完整 ROM 数据 (就地修改 sector/block hashtable 区域)
        dsi_header: DsiExtraFields 实例 (字段会被更新)
    """
    digest_sector_size = dsi_header.digest_sector_size
    block_sectorcount = dsi_header.digest_block_sectorcount
    if digest_sector_size <= 0 or block_sectorcount <= 0:
        raise ValueError("digest_sector_size 与 digest_block_sectorcount 必须大于 0")
    for name, start, size in (
        ("digest_ntr", dsi_header.digest_ntr_start, dsi_header.digest_ntr_size),
        ("digest_twl", dsi_header.digest_twl_start, dsi_header.digest_twl_size),
        ("sector_hashtable", dsi_header.sector_hashtable_start, dsi_header.sector_hashtable_size),
        ("block_hashtable", dsi_header.block_hashtable_start, dsi_header.block_hashtable_size),
    ):
        if start < 0 or size < 0 or start > len(rom_data) or size > len(rom_data) - start:
            raise ValueError(
                f"{name} 越界：offset=0x{start:X}, size=0x{size:X}, "
                f"ROM=0x{len(rom_data):X}"
            )
    if dsi_header.digest_ntr_size % digest_sector_size:
        raise ValueError("digest_ntr_size 不是 digest_sector_size 的整数倍")
    if dsi_header.digest_twl_size % digest_sector_size:
        raise ValueError("digest_twl_size 不是 digest_sector_size 的整数倍")

    # ---- sector digests ----
    sector_hashes = bytearray()

    # NTR 区域
    for position in range(
        dsi_header.digest_ntr_start,
        dsi_header.digest_ntr_start + dsi_header.digest_ntr_size,
        digest_sector_size,
    ):
        sector_hashes.extend(sha1_hmac(rom_data, position, digest_sector_size))

    # TWL 区域
    for position in range(
        dsi_header.digest_twl_start,
        dsi_header.digest_twl_start + dsi_header.digest_twl_size,
        digest_sector_size,
    ):
        sector_hashes.extend(sha1_hmac(rom_data, position, digest_sector_size))

    if len(sector_hashes) > dsi_header.sector_hashtable_size:
        raise ValueError(
            f"sector digest 超出预留表：0x{len(sector_hashes):X} > "
            f"0x{dsi_header.sector_hashtable_size:X}"
        )
    sector_start = dsi_header.sector_hashtable_start
    sector_end = sector_start + dsi_header.sector_hashtable_size
    rom_data[sector_start:sector_end] = b"\x00" * dsi_header.sector_hashtable_size
    rom_data[sector_start : sector_start + len(sector_hashes)] = sector_hashes

    # ---- block digests ----
    block_hashes = bytearray()
    block_size = block_sectorcount * HASH_SIZE
    for position in range(
        dsi_header.sector_hashtable_start,
        dsi_header.sector_hashtable_start + dsi_header.sector_hashtable_size,
        block_size,
    ):
        block_hashes.extend(sha1_hmac(rom_data, position, block_size))

    if len(block_hashes) > dsi_header.block_hashtable_size:
        raise ValueError(
            f"block digest 超出预留表：0x{len(block_hashes):X} > "
            f"0x{dsi_header.block_hashtable_size:X}"
        )
    block_start = dsi_header.block_hashtable_start
    block_end = block_start + dsi_header.block_hashtable_size
    rom_data[block_start:block_end] = b"\x00" * dsi_header.block_hashtable_size
    rom_data[block_start : block_start + len(block_hashes)] = block_hashes


def write_hashes(
    rom_data: bytearray,
    arm9_rom_offset: int,
    arm9_size: int,
    arm7_rom_offset: int,
    arm7_size: int,
    banner_offset: int,
    dsi_header: DsiExtraFields,
) -> None:
    """
    计算 6 组 HMAC-SHA1 签名并写入 dsi_header。

    对应 Rust: write_hashes(writer, header, dsi_header)

    Args:
        rom_data: 完整 ROM 数据
        arm9_rom_offset: ARM9 ROM 偏移
        arm9_size: ARM9 大小
        arm7_rom_offset: ARM7 ROM 偏移
        arm7_size: ARM7 大小
        banner_offset: Banner ROM 偏移
        dsi_header: DsiExtraFields 实例 (hmac_* 字段会被更新)
    """
    arm9_no_secure_start = 0x8000
    arm9_no_secure_size = arm9_size - (arm9_no_secure_start - arm9_rom_offset)
    if arm9_no_secure_size < 0:
        raise ValueError(
            f"ARM9 太短，无法计算 no-secure HMAC：offset=0x{arm9_rom_offset:X}, "
            f"size=0x{arm9_size:X}"
        )

    dsi_header.hmac_arm9 = sha1_hmac(rom_data, arm9_rom_offset, arm9_size)
    dsi_header.hmac_arm7 = sha1_hmac(rom_data, arm7_rom_offset, arm7_size)
    dsi_header.hmac_digest_master = sha1_hmac(
        rom_data,
        dsi_header.block_hashtable_start,
        dsi_header.block_hashtable_size,
    )
    dsi_header.hmac_icon_title = sha1_hmac(
        rom_data, banner_offset, dsi_header.banner_size
    )
    dsi_header.hmac_arm9i = sha1_hmac(
        rom_data, dsi_header.dsi9_rom_offset, dsi_header.dsi9_size
    )
    dsi_header.hmac_arm7i = sha1_hmac(
        rom_data, dsi_header.dsi7_rom_offset, dsi_header.dsi7_size
    )
    dsi_header.hmac_arm9_no_secure = sha1_hmac(
        rom_data,
        arm9_no_secure_start,
        arm9_no_secure_size,
    )
    # RSA 签名填充 (faraplay 行为)
    dsi_header.rsa_signature = b"\xAA" * 0x80


# ======================================================================
# 完整 DSi/TWL 重建
# ======================================================================


class DsiBuildError(RuntimeError):
    """输入 ROM 或重建布局不满足 DSi 构建契约。"""


@dataclass
class OriginalDsiState:
    """从未修改原版 ROM 读取并解密出的 DSi 构建输入。"""

    header: NDSHeader
    dsi_header: DsiExtraFields
    arm9i_plain: bytes
    arm7i_plain: bytes
    arm9i_has_footer: bool
    physical_rom_size: int


@dataclass
class DsiBuildReport:
    """构建完成后可打印/序列化的非版权结构信息。"""

    application_end_offset: int
    dsi9_rom_offset: int
    dsi9_size: int
    dsi7_rom_offset: int
    dsi7_size: int
    digest_ntr_size: int
    digest_twl_size: int
    sector_hashtable_start: int
    sector_hashtable_size: int
    block_hashtable_start: int
    block_hashtable_size: int
    modcrypt1_start: int
    modcrypt1_size: int
    modcrypt2_start: int
    modcrypt2_size: int
    total_rom_size: int
    physical_rom_size: int
    secure_area_crc: int
    header_crc: int


def align_up(value: int, alignment: int) -> int:
    """返回不小于 value 的最小 alignment 倍数。"""
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment 必须是 2 的幂")
    return (value + alignment - 1) & -alignment


def _pad_to_alignment(data: bytearray, alignment: int, fill: int = 0xFF) -> int:
    target = align_up(len(data), alignment)
    if target > len(data):
        data.extend(bytes((fill & 0xFF,)) * (target - len(data)))
    return target


def _pad_to_position(data: bytearray, position: int, fill: int = 0xFF) -> None:
    if position < len(data):
        raise DsiBuildError(
            f"不能向后回退填充：当前位置 0x{len(data):X}，目标 0x{position:X}"
        )
    if position > len(data):
        data.extend(bytes((fill & 0xFF,)) * (position - len(data)))


def _read_exact_at(handle: Any, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0:
        raise DsiBuildError(f"{label} 的偏移或大小为负数")
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise DsiBuildError(
            f"{label} 读取不足：offset=0x{offset:X}, expected=0x{size:X}, "
            f"actual=0x{len(data):X}"
        )
    return data


def _crypt_module_region(
    module: bytes,
    module_rom_offset: int,
    crypt_start: int,
    crypt_size: int,
    key: int,
    iv: int,
    label: str,
) -> bytes:
    """在独立 ARM9i/ARM7i 缓冲区内执行一个绝对 ROM modcrypt 范围。"""
    if crypt_size == 0:
        return module
    relative = crypt_start - module_rom_offset
    if relative < 0 or relative > len(module) or crypt_size > len(module) - relative:
        raise DsiBuildError(
            f"{label} 不完全位于模块内：module=0x{module_rom_offset:X}+0x{len(module):X}, "
            f"crypt=0x{crypt_start:X}+0x{crypt_size:X}"
        )
    if crypt_size % 16:
        raise DsiBuildError(f"{label} 大小 0x{crypt_size:X} 不是 AES block 的整数倍")
    result = bytearray(module)
    region = bytearray(result[relative : relative + crypt_size])
    aes_ctr(region, key, iv)
    result[relative : relative + crypt_size] = region
    return bytes(result)


def load_original_dsi_state(original_rom_path: str | Path) -> OriginalDsiState:
    """
    从用户自己的原版 ROM 读取 Header，并把 ARM9i/ARM7i 解成明文。

    只读取 Header 与两个 DSi CPU 模块，不把整张 256 MiB ROM 再复制到内存。
    """
    path = Path(original_rom_path)
    physical_size = path.stat().st_size
    with path.open("rb") as handle:
        header_blob = _read_exact_at(handle, 0, TWL_HEADER_SIZE, "TWL Header")
        header = NDSHeader.parse(header_blob[:NDS_HEADER_SIZE])
        if not header.is_dsi:
            raise DsiBuildError(
                f"原版 ROM unitcode={header.unitcode}，不是 DSi 增强标题"
            )
        dsi_header = DsiExtraFields.parse(
            header_blob[
                DSI_EXTRA_FIELDS_OFFSET : DSI_EXTRA_FIELDS_OFFSET + DSI_EXTRA_FIELDS_SIZE
            ]
        )

        for label, offset, size in (
            ("ARM9i", dsi_header.dsi9_rom_offset, dsi_header.dsi9_size),
            ("ARM7i", dsi_header.dsi7_rom_offset, dsi_header.dsi7_size),
        ):
            if offset <= 0 or size <= 0 or offset > physical_size or size > physical_size - offset:
                raise DsiBuildError(
                    f"原版 {label} 范围无效：offset=0x{offset:X}, size=0x{size:X}, "
                    f"ROM=0x{physical_size:X}"
                )

        arm9i_has_footer = False
        footer_probe_offset = dsi_header.dsi9_rom_offset + dsi_header.dsi9_size
        if footer_probe_offset + 4 <= physical_size:
            footer_magic = _read_exact_at(handle, footer_probe_offset, 4, "ARM9i footer probe")
            arm9i_has_footer = footer_magic == b"\x21\x06\xC0\xDE"

        arm9i_read_size = dsi_header.dsi9_size + (0x0C if arm9i_has_footer else 0)
        arm9i = _read_exact_at(
            handle, dsi_header.dsi9_rom_offset, arm9i_read_size, "ARM9i"
        )
        arm7i = _read_exact_at(
            handle, dsi_header.dsi7_rom_offset, dsi_header.dsi7_size, "ARM7i"
        )

    if header.dsi_flags & 0x02:
        key, iv1, iv2 = get_key_ivs(
            header.gamecode,
            dsi_header.hmac_arm9,
            dsi_header.hmac_arm7,
            dsi_header.hmac_arm9i,
        )
        arm9i = _crypt_module_region(
            arm9i,
            dsi_header.dsi9_rom_offset,
            dsi_header.modcrypt1_start,
            dsi_header.modcrypt1_size,
            key,
            iv1,
            "modcrypt1/ARM9i",
        )
        arm7i = _crypt_module_region(
            arm7i,
            dsi_header.dsi7_rom_offset,
            dsi_header.modcrypt2_start,
            dsi_header.modcrypt2_size,
            key,
            iv2,
            "modcrypt2/ARM7i",
        )

    return OriginalDsiState(
        header=header,
        dsi_header=dsi_header,
        arm9i_plain=arm9i,
        arm7i_plain=arm7i,
        arm9i_has_footer=arm9i_has_footer,
        physical_rom_size=physical_size,
    )


def _relocate_modcrypt(
    old_module_start: int,
    old_crypt_start: int,
    crypt_size: int,
    new_module_start: int,
    new_module_size: int,
    label: str,
) -> int:
    if crypt_size == 0:
        return 0
    relative = old_crypt_start - old_module_start
    if relative < 0 or relative > new_module_size or crypt_size > new_module_size - relative:
        raise DsiBuildError(
            f"{label} 无法随模块重定位：relative=0x{relative:X}, "
            f"size=0x{crypt_size:X}, module_size=0x{new_module_size:X}"
        )
    return new_module_start + relative


def rebuild_dsi_rom(
    ntr_rom_data: bytes | bytearray,
    original_rom_path: str | Path,
) -> tuple[bytes, DsiBuildReport]:
    """
    把 ndspy 生成的有效 NTR 内容扩展为完整 DSi/TWL ROM。

    运算顺序与 faraplay/dearlystars_tool 0.5.2 一致：预留 digest 表 → 写入
    明文 ARM9i/ARM7i → 临时加密 Secure Area → 生成两级 digest 与 HMAC →
    恢复最终解密态 Secure Area → 用新 HMAC 重新 modcrypt → 写回 0x1000
    字节 TWL Header。
    """
    state = load_original_dsi_state(original_rom_path)
    out = bytearray(ntr_rom_data)
    if len(out) < ROM_HEADER_SIZE:
        raise DsiBuildError(
            f"NTR 构建结果过短：0x{len(out):X} < 0x{ROM_HEADER_SIZE:X}"
        )

    header = NDSHeader.parse(out[:NDS_HEADER_SIZE])
    dsi_header = state.dsi_header
    old_dsi9_rom_offset = dsi_header.dsi9_rom_offset
    old_dsi7_rom_offset = dsi_header.dsi7_rom_offset
    old_modcrypt1_start = dsi_header.modcrypt1_start
    old_modcrypt1_size = dsi_header.modcrypt1_size
    old_modcrypt2_start = dsi_header.modcrypt2_start
    old_modcrypt2_size = dsi_header.modcrypt2_size
    if header.gamecode != state.header.gamecode:
        raise DsiBuildError(
            f"Game Code 不一致：NTR={header.gamecode!r}, 原版={state.header.gamecode!r}"
        )
    if not header.is_dsi:
        raise DsiBuildError("ndspy 输出丢失了 DSi unitcode")
    if header.arm9_rom_offset != 0x4000:
        raise DsiBuildError(
            f"本项目要求 ARM9 位于 0x4000，实际为 0x{header.arm9_rom_offset:X}"
        )
    for label, offset, size in (
        ("ARM9", header.arm9_rom_offset, header.arm9_size),
        ("ARM7", header.arm7_rom_offset, header.arm7_size),
        ("Banner", header.banner_offset, dsi_header.banner_size),
    ):
        if offset < 0 or size < 0 or offset > len(out) or size > len(out) - offset:
            raise DsiBuildError(
                f"NTR {label} 范围越界：offset=0x{offset:X}, size=0x{size:X}, "
                f"NTR=0x{len(out):X}"
            )

    # ndspy 输出的末尾就是最后一个有效文件。先对齐，再紧跟两张摘要表。
    _pad_to_alignment(out, SECTOR_ALIGNMENT, 0xFF)
    dsi_header.digest_sector_size = SECTOR_ALIGNMENT
    dsi_header.digest_block_sectorcount = BLOCK_SECTORCOUNT
    dsi_header.digest_ntr_start = 0x4000
    dsi_header.digest_ntr_size = len(out) - dsi_header.digest_ntr_start

    arm9i_size = align_up(len(state.arm9i_plain), 4)
    arm7i_size = align_up(len(state.arm7i_plain), 4)
    dsi_header.digest_twl_size = (
        align_up(arm9i_size, SECTOR_ALIGNMENT)
        + align_up(arm7i_size, SECTOR_ALIGNMENT)
    )

    dsi_header.sector_hashtable_start = _pad_to_alignment(
        out, SECTOR_ALIGNMENT, 0xFF
    )
    sectors_count = (
        dsi_header.digest_ntr_size + dsi_header.digest_twl_size
    ) // dsi_header.digest_sector_size
    sectors_count_padded = align_up(sectors_count, BLOCK_SECTORCOUNT)
    dsi_header.sector_hashtable_size = sectors_count_padded * HASH_SIZE
    out.extend(b"\x00" * dsi_header.sector_hashtable_size)

    dsi_header.block_hashtable_start = _pad_to_alignment(
        out, SECTOR_ALIGNMENT, 0xFF
    )
    dsi_header.block_hashtable_size = (
        dsi_header.sector_hashtable_size // dsi_header.digest_block_sectorcount
    )
    out.extend(b"\x00" * dsi_header.block_hashtable_size)
    _pad_to_alignment(out, FILE_ALIGNMENT, 0xFF)

    header.application_end_offset = len(out)
    header.rom_header_size = ROM_HEADER_SIZE

    # faraplay 会复制新 ARM9 的 0x8000 sector 三次后再放置 ARM9i。
    junk_data = bytes(out[0x8000:0x9000])
    if len(junk_data) != 0x1000:
        raise DsiBuildError("无法读取用于 TWL 区的 0x8000 junk sector")
    _pad_to_alignment(out, DSI_ALIGNMENT, 0xFF)
    out.extend(junk_data * 3)

    dsi_header.dsi9_rom_offset = _pad_to_alignment(out, SECTOR_ALIGNMENT, 0xFF)
    out.extend(state.arm9i_plain)
    _pad_to_position(out, dsi_header.dsi9_rom_offset + arm9i_size, 0xFF)
    dsi_header.dsi9_size = arm9i_size

    dsi_header.dsi7_rom_offset = _pad_to_alignment(out, SECTOR_ALIGNMENT, 0xFF)
    out.extend(state.arm7i_plain)
    _pad_to_position(out, dsi_header.dsi7_rom_offset + arm7i_size, 0xFF)
    dsi_header.dsi7_size = arm7i_size
    dsi_header.digest_twl_start = dsi_header.dsi9_rom_offset

    _pad_to_alignment(out, SECTOR_ALIGNMENT, 0xFF)
    dsi_header.total_rom_size = len(out)

    # 保留原版 modcrypt 在各模块内的相对范围，并随新模块位置重定位。
    dsi_header.modcrypt1_start = _relocate_modcrypt(
        old_dsi9_rom_offset,
        old_modcrypt1_start,
        old_modcrypt1_size,
        dsi_header.dsi9_rom_offset,
        dsi_header.dsi9_size,
        "modcrypt1",
    )
    dsi_header.modcrypt2_start = _relocate_modcrypt(
        old_dsi7_rom_offset,
        old_modcrypt2_start,
        old_modcrypt2_size,
        dsi_header.dsi7_rom_offset,
        dsi_header.dsi7_size,
        "modcrypt2",
    )

    ntr_region_units = align_up(
        header.application_end_offset, DSI_ALIGNMENT
    ) >> 19
    if ntr_region_units > 0xFFFF:
        raise DsiBuildError("DSi NTR/TWL region 单位溢出 u16")
    header.dsi_ntr_rom_region_end = ntr_region_units
    header.dsi_twl_rom_region_start = ntr_region_units

    minimum_capacity = 1 << max(17, (dsi_header.total_rom_size - 1).bit_length())
    original_capacity = 1 << (state.header.devicecap + 17)
    physical_target = max(minimum_capacity, original_capacity, state.physical_rom_size)
    if physical_target & (physical_target - 1):
        physical_target = 1 << physical_target.bit_length()
    header.devicecap = physical_target.bit_length() - 18
    _pad_to_position(out, physical_target, 0xFF)

    # HMAC/digest 的规范视图是：Secure Area 加密、ARM9i/ARM7i 明文。
    secure_was_plain = encrypt_secure_area(out, header.gamecode)
    if not secure_was_plain:
        # 输入若已加密，也必须能按本作 gamecode 正确解密；稍后统一恢复明文。
        probe = bytes(out[0x4000:0x8000])
        try:
            decrypt_arm9(int.from_bytes(header.gamecode, "little"), probe)
        except ValueError as exc:
            raise DsiBuildError("ARM9 Secure Area 既非已知明文，也不是有效密文") from exc

    write_digests(out, dsi_header)
    write_hashes(
        out,
        header.arm9_rom_offset,
        header.arm9_size,
        header.arm7_rom_offset,
        header.arm7_size,
        header.banner_offset,
        dsi_header,
    )

    # 最终 ROM 延续当前已通过 NTR 实机测试的解密 Secure Area 状态。
    decrypt_secure_area(out, header.gamecode)
    header.secure_area_crc = crc16_header(bytes(out[0x4000:0x8000]))

    if header.dsi_flags & 0x02:
        modcrypt(out, header.gamecode, dsi_header)
    elif dsi_header.modcrypt1_size or dsi_header.modcrypt2_size:
        raise DsiBuildError("Header 未启用 modcrypt，但 modcrypt 区域大小非零")

    header_bytes = header.build(update_crc=True)
    out[0:NDS_HEADER_SIZE] = header_bytes
    out[
        DSI_EXTRA_FIELDS_OFFSET : DSI_EXTRA_FIELDS_OFFSET + DSI_EXTRA_FIELDS_SIZE
    ] = dsi_header.build()

    report = DsiBuildReport(
        application_end_offset=header.application_end_offset,
        dsi9_rom_offset=dsi_header.dsi9_rom_offset,
        dsi9_size=dsi_header.dsi9_size,
        dsi7_rom_offset=dsi_header.dsi7_rom_offset,
        dsi7_size=dsi_header.dsi7_size,
        digest_ntr_size=dsi_header.digest_ntr_size,
        digest_twl_size=dsi_header.digest_twl_size,
        sector_hashtable_start=dsi_header.sector_hashtable_start,
        sector_hashtable_size=dsi_header.sector_hashtable_size,
        block_hashtable_start=dsi_header.block_hashtable_start,
        block_hashtable_size=dsi_header.block_hashtable_size,
        modcrypt1_start=dsi_header.modcrypt1_start,
        modcrypt1_size=dsi_header.modcrypt1_size,
        modcrypt2_start=dsi_header.modcrypt2_start,
        modcrypt2_size=dsi_header.modcrypt2_size,
        total_rom_size=dsi_header.total_rom_size,
        physical_rom_size=len(out),
        secure_area_crc=header.secure_area_crc,
        header_crc=header.header_crc,
    )
    return bytes(out), report


def verify_dsi_integrity(rom_data: bytes | bytearray) -> dict[str, Any]:
    """
    重新构造 DSi 规范哈希视图，验证两级 digest、全部 HMAC 与 modcrypt。

    返回的字典只含布尔值、大小和 SHA-256，不导出游戏内容。
    """
    rom = bytes(rom_data)
    if len(rom) < TWL_HEADER_SIZE:
        raise DsiBuildError("ROM 短于 0x1000 字节 TWL Header")
    header = NDSHeader.parse(rom[:NDS_HEADER_SIZE])
    if not header.is_dsi:
        raise DsiBuildError("不是 DSi 增强 ROM")
    dsi_header = DsiExtraFields.parse(
        rom[
            DSI_EXTRA_FIELDS_OFFSET : DSI_EXTRA_FIELDS_OFFSET + DSI_EXTRA_FIELDS_SIZE
        ]
    )

    canonical = bytearray(rom)
    if header.dsi_flags & 0x02:
        modcrypt(canonical, header.gamecode, dsi_header)

    arm9i_plain = bytes(
        canonical[
            dsi_header.dsi9_rom_offset : dsi_header.dsi9_rom_offset + dsi_header.dsi9_size
        ]
    )
    arm7i_plain = bytes(
        canonical[
            dsi_header.dsi7_rom_offset : dsi_header.dsi7_rom_offset + dsi_header.dsi7_size
        ]
    )
    final_secure_plain = (
        len(rom) >= 0x8000
        and struct.unpack_from("<Q", rom, 0x4000)[0] == 0xE7FFDEFFE7FFDEFF
    )
    if final_secure_plain:
        encrypt_secure_area(canonical, header.gamecode)

    stored_sector = bytes(
        rom[
            dsi_header.sector_hashtable_start :
            dsi_header.sector_hashtable_start + dsi_header.sector_hashtable_size
        ]
    )
    stored_block = bytes(
        rom[
            dsi_header.block_hashtable_start :
            dsi_header.block_hashtable_start + dsi_header.block_hashtable_size
        ]
    )
    stored_hmacs = {
        "arm9": dsi_header.hmac_arm9,
        "arm7": dsi_header.hmac_arm7,
        "digest_master": dsi_header.hmac_digest_master,
        "icon_title": dsi_header.hmac_icon_title,
        "arm9i": dsi_header.hmac_arm9i,
        "arm7i": dsi_header.hmac_arm7i,
        "arm9_no_secure": dsi_header.hmac_arm9_no_secure,
    }

    write_digests(canonical, dsi_header)
    expected_sector = bytes(
        canonical[
            dsi_header.sector_hashtable_start :
            dsi_header.sector_hashtable_start + dsi_header.sector_hashtable_size
        ]
    )
    expected_block = bytes(
        canonical[
            dsi_header.block_hashtable_start :
            dsi_header.block_hashtable_start + dsi_header.block_hashtable_size
        ]
    )
    write_hashes(
        canonical,
        header.arm9_rom_offset,
        header.arm9_size,
        header.arm7_rom_offset,
        header.arm7_size,
        header.banner_offset,
        dsi_header,
    )
    expected_hmacs = {
        "arm9": dsi_header.hmac_arm9,
        "arm7": dsi_header.hmac_arm7,
        "digest_master": dsi_header.hmac_digest_master,
        "icon_title": dsi_header.hmac_icon_title,
        "arm9i": dsi_header.hmac_arm9i,
        "arm7i": dsi_header.hmac_arm7i,
        "arm9_no_secure": dsi_header.hmac_arm9_no_secure,
    }
    hmac_ok = {
        name: hmac.compare_digest(stored_hmacs[name], expected_hmacs[name])
        for name in stored_hmacs
    }
    sector_ok = hmac.compare_digest(stored_sector, expected_sector)
    block_ok = hmac.compare_digest(stored_block, expected_block)
    return {
        "secure_area_final_decrypted": final_secure_plain,
        "sector_hashtable_ok": sector_ok,
        "block_hashtable_ok": block_ok,
        "hmac_ok": hmac_ok,
        "all_ok": final_secure_plain and sector_ok and block_ok and all(hmac_ok.values()),
        "arm9i_plain_size": len(arm9i_plain),
        "arm9i_plain_sha256": hashlib.sha256(arm9i_plain).hexdigest(),
        "arm7i_plain_size": len(arm7i_plain),
        "arm7i_plain_sha256": hashlib.sha256(arm7i_plain).hexdigest(),
    }
