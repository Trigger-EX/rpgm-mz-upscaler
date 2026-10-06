# Generates committed test fixtures with REAL Ruby: ruby tools/make_marshal_fixtures.rb tests/fixtures/rgss
require 'fileutils'
out = ARGV[0] || 'tests/fixtures/rgss'
FileUtils.mkdir_p(out)
def w(out, name, *objs) File.binwrite(File.join(out, name), objs.map { |o| Marshal.dump(o) }.join) end

class Table
  def initialize(x, y, z, cells) @x, @y, @z, @cells = x, y, z, cells end
  def _dump(lv) [3, @x, @y, @z, @cells.size].pack('l5') + @cells.pack('s*') end
  def self._load(s) a = s.unpack('l5'); new(a[1], a[2], a[3], s[20..-1].unpack('s*')) end
end
class Color
  def initialize(r, g, b, a) @c = [r, g, b, a] end
  def _dump(lv) @c.pack('d4') end
  def self._load(s) new(*s.unpack('d4')) end
end
class Wrapped
  def initialize(v) @v = v end
  def marshal_dump() [@v, 1] end
  def marshal_load(a) @v = a[0] end
end
module Mixin; end
class MyStr < String; end
class MyArr < Array; end
class MyHash < Hash; end
Pt = Struct.new(:x, :y)

class Thing; def initialize; @name = 'ソード'; @hp = 100; @ratio = 1.0 / 3; @tags = [:a, :b, :a]; @self = self; end; end

shared = 'shared string'
h = Hash.new(7); h[1] = 'one'; h[:k] = [shared, shared]
sw = [nil, true, false, true]
types = [
  nil, true, false, 0, 1, -1, 122, 123, -123, -124, 255, 256, -256, -257, 65535, 65536, 2**30 - 1, -(2**30), 2**30, -(2**30) - 1,
  2**64, -(2**64), 2**70 + 12345,
  0.0, -0.0, 0.1, 1.0 / 3, 1e20, -1.5e-10, 123456789.123456789, Float::INFINITY, -Float::INFINITY, Float::NAN,
  'ascii', 'ひらがな漢字', 'bin'.b, "\xFF\xFE".b, 'sjis'.encode('Shift_JIS'), ''.b,
  :sym, :sym, :'日本語', [:sym, :other, :sym],
  [shared, shared, 'x'], h, Hash.new, { 'k' => 1, [1, 2, 'A'] => true, 1.5 => nil },
  Thing.new, Pt.new(1, 'two'), Table.new(2, 3, 1, [1, -2, 3, 4, 5, 6]), Color.new(1.0, 2.0, 3.0, 4.0),
  Wrapped.new('inner'), /abc+/i, /日本/, String, Kernel, Comparable,
  MyStr.new('sub'), MyArr.new([1, 2]), MyHash[1, 2], 'ext'.dup.extend(Mixin),
  sw, [sw, sw], (1..10), 3.0, [3.0, 3.0], [1.5, 1.5]
]
w(out, 'types.bin', types)
# individual streams too, for load_all
w(out, 'multi.bin', { characters: [['Hero', 1]], playtime_s: 1234 }, { system: 1, gold: 500 }, [1, 2, 3])
