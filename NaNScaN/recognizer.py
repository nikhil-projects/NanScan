# coding=iso-8859-1
#   Copyright (C) 2008 by Albert Cervera i Areny
#   albert@nan-tic.com
#
#   This program is free software; you can redistribute it and/or modify 
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or 
#   (at your option) any later version. 
#
#   This program is distributed in the hope that it will be useful, 
#   but WITHOUT ANY WARRANTY; without even the implied warranty of 
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the 
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License 
#   along with this program; if not, write to the
#   Free Software Foundation, Inc.,
#   59 Temple Place - Suite 330, Boston, MA  02111-1307, USA. 

from PyQt4.QtCore import *
from barcode import *
from ocr import *

from template import *
from document import *
from trigram import *
from hamming import *
from translator import *

import tempfile

class Analyze(QThread):
	def __init__(self, analyzer, image, parent=None):
		QThread.__init__(self, parent)
		self.analyzer = analyzer
		self.image = image

	def run(self):
		self.analyzer.scan( self.image )


class Recognizer(QObject):
	def __init__(self, parent=None):
		QObject.__init__(self, parent)
		self.barcode = Barcode()
		self.ocr = Ocr()
		self.image = None

	## @brief Returns the text of a given region of the image. 
	def textInRegion(self, region, type=None):
		if type == 'barcode':
			return self.barcode.textInRegion( region )
		elif type == 'text':
			return self.ocr.textInRegion( region )
		else:
			return None

	## @brief Returns the bounding rectangle of the text returned by textInRegion for
	# the given region.
	def featureRectInRegion(self, region, type=None):
		if type == 'barcode':
			return self.barcode.featureRectInRegion( region )
		elif type == 'text':
			return self.ocr.featureRectInRegion( region )
		else:
			return None

	def boxes(self, type):
		if type == 'barcode':
			return self.barcode.boxes
		elif type == 'text':
			return self.ocr.boxes
		else:
			return None

	def analyzersAvailable(self):
		return ['barcode', 'text']
		
	# Synchronous
	def recognize(self, image):
		self.image = image
		self.barcode.scan( image )
		self.ocr.scan( image )

	## @brief Asynchronous: Starts analyzers in background threads. Emits finished() at the end
	def startRecognition(self, image):
		self.image = image
		self.ocrThread = Analyze( self.ocr, image, self )
		self.barcodeThread = Analyze( self.barcode, image, self )
		self.connect( self.ocrThread, SIGNAL('finished()'), self.recognitionFinished )
		self.connect( self.barcodeThread, SIGNAL('finished()'), self.recognitionFinished )
		self.ocrThread.start()
		self.barcodeThread.start()
		
	def recognitionFinished(self):
		if self.ocrThread.isFinished() and self.barcodeThread.isFinished():
			self.emit( SIGNAL('finished()') )

	def filter(self, value, filterType):
		numeric = '0123456789'
		alphabetic = 'abc�defghijklmn�opqrstuvwxyz'
		if filterType == 'numeric':
			return u''.join( [x for x in value if x in numeric] )
		elif filterType == 'alphabetic':
			return u''.join( [x for x in value if x in alphabetic] )
		elif filterType == 'alphanumeric':
			return u''.join( [x for x in value if x in numeric+alphabetic] )
		elif filterType == 'none':
			return value
		else:
			print "Filter type '%s' not implemented" % filterType
			return value

	## @brief Extracts the information of the recognized image using the
	# given template. 
	# Optionally an x and y offset can be applied to the template before
	# extracting data.
	# Note that the image must have been scanned (using scan() or startScan()) 
	# before using this function.
	def extractWithTemplate(self, template, xOffset = 0, yOffset = 0): 
		if not template:
			return None
		document = Document()
		for templateBox in template.boxes:
			if not templateBox.text:
				continue

			rect = QRectF( templateBox.rect )
			rect.translate( xOffset, yOffset )

			text = self.textInRegion( rect, templateBox.recognizer )
			text = self.filter( text, templateBox.filter )
			documentBox = DocumentBox()
			documentBox.text = text
			documentBox.templateBox = templateBox
			document.addBox( documentBox )
		return document

	## @brief Tries to find out the best template in 'templates' for the current
	# image.
	# Use the optional parameter 'offset' to specify up to how many millimeters
	# the template should be translated to find the best match. Setting this to
	# 5 (the default) will make the template move 5 millimeter to the right,
	# 5 to the left, 5 to the top and 5 to the bottom. This means 121 positions
	# per template.
	# Note that the image must have been scanned (using scan() or startScan()) 
	# before using this function.
	#
	# TODO: Using offsets to find the best template is easy but highly inefficient.
	#  a smarter solution should be implemented.
	def findMatchingTemplateByOffset( self, templates, offset = 5 ):
		max = 0
		best = {
			'template': None,
			'document': Document(),
			'xOffset' : 0,
			'yOffset' : 0
		}
		for template in templates:
			if not template.boxes:
				continue
			# Consider up to 5 millimeter offset
			for xOffset in range(-5,6):
				for yOffset in range(-5,6):
					score = 0
					matcherBoxes = 0
					currentDocument = self.extractWithTemplate( template, xOffset, yOffset )
					for documentBox in currentDocument.boxes:
						templateBox = documentBox.templateBox
						if documentBox.templateBox.type != 'matcher':
							print "Jumping %s due to type %s" % ( templateBox.name, templateBox.type )
							continue
						matcherBoxes += 1
						similarity = Trigram.trigram( documentBox.text, templateBox.text )
						score += similarity
					score = score / matcherBoxes
					if score > max:
						max = score
						best = { 
							'template': template,
							'document': currentDocument,
							'xOffset' : xOffset,
							'yOffset' : yOffset
						}
					print "Template %s has score %s with offset (%s,%s)" % (template.name, score, xOffset, yOffset)
		return best


	## @brief Tries to find out the best template in 'templates' for the current
	# image.
	# This algorithm starts by looking for template boxes of type 'matching' in the
	# text and then looks if the relative positions of the new document and template
	# boxes are similar. This is intended to be faster than exhaustive algorithm used
	# in findMatchingTemplateByOffset().
	#
	# Note that the image must have been scanned (using scan() or startScan()) 
	# before using this function.
	#
	# TODO: Using offsets to find the best template is easy but highly inefficient.
	#  a smarter solution should be implemented.
	def findMatchingTemplateByText( self, templates ):
		max = 0
		best = {
			'template': None,
			'document': Document(),
			'xOffset' : 0,
			'yOffset' : 0
		}
		for template in templates:
			if not template.boxes:
				continue
			# Find out template's offset
			offset = self.findTemplateOffset( template )
			if not offset:
				continue

			score = 0
			matcherBoxes = 0
			# Apply template with offset found
			currentDocument = self.extractWithTemplate( template, offset.x(), offset.y() )
			for documentBox in currentDocument.boxes:
				templateBox = documentBox.templateBox
				if documentBox.templateBox.type != 'matcher':
					continue
				matcherBoxes += 1
				similarity = Trigram.trigram( documentBox.text, templateBox.text )
				score += similarity
			score = score / matcherBoxes
			if score > max:
				max = score
				best = { 
					'template': template,
					'document': currentDocument,
					'xOffset' : offset.x(),
					'yOffset' : offset.y()
				}
		return best

	## @brief Returns a QPoint with the offset that needs to be applied to the given
	# template to best fit the current image.
	def findTemplateOffset( self, template ):
		if not template.boxes:
			return QPoint( 0, 0 )

		lines = self.ocr.textLinesWithSpaces()

		# Create a default translator only once
		translator = Translator()

		# This list will keep a pointer to each template box of type 'matcher'
		matchers = []
		for templateBox in template.boxes:
			if templateBox.type != 'matcher':
				continue

			templateBox.ranges = Range.extractAllRangesFromDocument(lines, len(templateBox.text))
			for ran in templateBox.ranges:
				text = ran.text()
				value = Hamming.hamming( text, templateBox.text, translator )
				ran.distance = value
			templateBox.ranges.sort( rangeDistanceComparison )

			if templateBox.ranges:
				bestRange = templateBox.ranges[0]
				print "The best match for template box '%s' is '%s'" % (templateBox.text, bestRange.text() )
			matchers.append( templateBox )

		# Once we have all ranges sorted for each template box we search which
		# range combination matches the template.
		iterator = TemplateBoxRangeIterator( matchers )
		i = 0
		for ranges in iterator:
			documentBoxCenter = ranges[0].rect().center()
			templateBoxCenter = matchers[0].featureRect.center()
			diff = templateBoxCenter - documentBoxCenter
			print "Difference: ", diff
			found = True
			for pos in range(1,len(ranges)):
				documentBoxCenter = ranges[pos].rect().center()
				templateBoxCenter = matchers[pos].featureRect.center()
				d = templateBoxCenter - documentBoxCenter
				# If difference of relative positions of boxes between 
				# template and document are bigger than 5mm we discard 
				# the ranges
				print "Difference in loop: ", d
				if ( abs(d.x()) + 5.0 > abs(diff.x()) ):
					found = False
					break
				if ( abs(d.y()) + 5.0 > abs(diff.y()) ):
					found = False
					break
			if found:
				break
			i += 1
			if i > 10:
				break
		if found:
			return diff
		else:
			return None


class TemplateBoxRangeIterator:
	def __init__(self, boxes):
		self.boxes = boxes
		self.pos = [0] * len(self.boxes)
		self.loopPos = [0] * len(self.boxes)
		self.added = None

	def __iter__(self):
		return self

	def next(self):
		result = []	
		for x in range(len(self.boxes)):
			result.append( self.boxes[x].ranges[ self.pos[x] ] )

		print '----'
		print (u', '.join( [x.text() for x in result] )).encode('ascii', 'ignore')
		print self.pos
		print self.loopPos
		if self.loopPos == self.pos:
			# Search next value to add
			value = float('infinity')
			pos = 0
			for x in range(len(self.pos)):
				if x >= len(self.boxes[x].ranges) - 1:
					continue
				if self.pos[x] < value:
					value = self.pos[x]
					self.added = x
			# If value is Infinity it means that we reached the end
			# of all possible iterations
			if value == float('infinity'):
				raise StopIteration

			self.pos[self.added] += 1
			self.loopPos = [0] * len(self.boxes)
			self.loopPos[self.added] = self.pos[self.added]
		else:
			for x in range(len(self.loopPos)):
				if x == self.added:
					continue		
				if self.loopPos[x] < self.pos[x]:
					self.loopPos[x] += 1
					break
		return result

def rangeDistanceComparison(x, y):
	if x.distance > y.distance:
		return 1
	elif x.distance < y.distance:
		return -1
	else:
		return 0

## @brief This class represents a group of characters in a document.
class Range:
	def __init__(self):
		self.line = 0
		self.pos = 0
		self.length = 0
		self.document = None

	## @brief Returns a unicode string with the text of the current range
	def text(self):
		line = self.document[self.line]
		chars = line[self.pos:self.pos + self.length]
		return u''.join( [x.character for x in chars] )

	## @brief Returns the bounding rectangle of the text in the range
	def rect(self):
		line = self.document[self.line]
		chars = line[self.pos:self.pos + self.length]
		rect = QRectF()
		for c in chars:
			rect = rect.united( c.box )
		return rect

	## @brief Returns a list with all possible ranges of size length of the 
	# given document
	@staticmethod
	def extractAllRangesFromDocument(lines, length):
		if length <= 0:
			return []
		ranges = []
		for line in range(len(lines)):
			if length > len(lines[line]):
				ran = Range()
				ran.line = line
				ran.pos = 0
				ran.length = len(lines[line])
				ran.document = lines
				ranges.append( ran )
				continue
			for pos in range(len(lines[line]) - length):
				ran = Range()
				ran.line = line
				ran.pos = pos
				ran.length = length
				ran.document = lines
				ranges.append( ran )
		return ranges
