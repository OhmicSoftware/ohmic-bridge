#--------------------------------------------------------------------------------
# Constants used in AbletonOSC
#--------------------------------------------------------------------------------

OSC_LISTEN_PORT = 11002
OSC_RESPONSE_PORT = 11003

# Maximum encoded payload bytes per reply. macOS caps a UDP datagram at
# 9,216 bytes by default (net.inet.udp.maxdgram); with the OSC wrapper
# overhead under 100 bytes, this budget keeps every reply under that cap.
# Endpoints whose full answer can exceed it page or chunk against it.
MAX_REPLY_BYTES = 8192
