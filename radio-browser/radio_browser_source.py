#    This file is part of Radio-Browser-Plugin for Rhythmbox.
#
#    Copyright (C) 2009 <segler_alex@web.de>
#
#    Radio-Browser-Plugin is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    Radio-Browser-Plugin is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with Radio-Browser-Plugin.  If not, see <http://www.gnu.org/licenses/>.

import rb
import rhythmdb
import gobject
import httplib
import gtk
import gconf
import os
import subprocess
import threading
import hashlib
import urllib
import webbrowser
import Queue
import pickle
import datetime
import math
import urllib2

import xml.sax.saxutils

from radio_station import RadioStation
from record_process import RecordProcess

from feed import Feed
from local_handler import FeedLocal
from icecast_handler import FeedIcecast
from shoutcast_handler import FeedShoutcast
from shoutcast_handler import ShoutcastRadioStation
from board_handler import FeedBoard
from board_handler import BoardHandler
from radiotime_handler import FeedRadioTime
from radiotime_handler import FeedRadioTimeLocal

#TODO: should not be defined here, but I don't know where to get it from. HELP: much apreciated
RB_METADATA_FIELD_TITLE = 0
RB_METADATA_FIELD_GENRE = 4
RB_METADATA_FIELD_BITRATE = 20
BOARD_ROOT = "http://www.radio-browser.info/"
RECENTLY_USED_FILENAME = "recently2.bin"
BOOKMARKS_FILENAME = "bookmarks.bin"

class RadioBrowserSource(rb.StreamingSource):
	__gproperties__ = {
		'plugin': (rb.Plugin, 'plugin', 'plugin', gobject.PARAM_WRITABLE|gobject.PARAM_CONSTRUCT_ONLY),
	}

	def __init__(self):
		self.hasActivated = False
		rb.StreamingSource.__init__(self,name="RadioBrowserPlugin")

	def do_set_property(self, property, value):
		if property.name == 'plugin':
			self.plugin = value

	""" return list of actions that should be displayed in toolbar """
	def do_impl_get_ui_actions(self):
		return ["UpdateList","ClearIconCache"]

	def do_impl_get_status(self):
		if self.updating:
			progress = -1.0
			if self.load_total_size > 0:
				progress = min (float(self.load_current_size) / self.load_total_size, 1.0)
			return (self.load_status,None,progress)
		else:
			return (_("Nothing to do"),None,2.0)

	def update_download_status(self,filename,current, total):
		self.load_current_size = current
		self.load_total_size = total
		self.load_status = _("Loading %(url)s") % {'url':filename}

		gtk.gdk.threads_enter()
		self.notify_status_changed()
		gtk.gdk.threads_leave()

	""" on source actiavation, e.g. double click on source or playing something in this source """
	def do_impl_activate(self):
		# first time of activation -> add graphical stuff
		if not self.hasActivated:
			self.shell = self.get_property('shell')
			self.db = self.shell.get_property('db')
			self.entry_type = self.get_property('entry-type')
			self.hasActivated = True

			# add listener for stream infos
			sp = self.shell.get_player ()
			#sp.connect ('playing-song-changed',self.playing_entry_changed)
			#sp.connect ('playing-changed',self.playing_changed)
			#sp.connect ('playing-song-property-changed',self.playing_song_property_changed)
			sp.props.player.connect("info",self.info_available)

			# create cache dir
			self.cache_dir = rb.find_user_cache_file("radio-browser")
			if os.path.exists(self.cache_dir) is False:
				os.makedirs(self.cache_dir, 0700)
			self.icon_cache_dir = os.path.join(self.cache_dir,"icons")
			if os.path.exists(self.icon_cache_dir) is False:
				os.makedirs(self.icon_cache_dir,0700)
			self.updating = False
			self.load_current_size = 0
			self.load_total_size = 0
			self.load_status = ""

			# create the model for the view
			self.filter_entry = gtk.Entry()
			self.filter_entry.connect("changed",self.filter_entry_changed)

			self.filter_entry_bitrate = gtk.SpinButton()
			self.filter_entry_bitrate.set_range(32,512)
			self.filter_entry_bitrate.set_value(64)
			self.filter_entry_bitrate.set_increments(32,32)
			self.filter_entry_bitrate.connect("changed",self.filter_entry_changed)

			self.filter_entry_genre = gtk.Entry()
			#cell = gtk.CellRendererText()
			#self.filter_entry_genre.pack_start(cell, True)
			#self.filter_entry_genre.add_attribute(cell, 'text', 0)
			self.filter_entry_genre.connect("changed",self.filter_entry_changed)

			self.tree_store = gtk.TreeStore(str,object)
			self.sorted_list_store = gtk.TreeModelSort(self.tree_store)
			#self.filtered_list_store = self.sorted_list_store.filter_new()
			#self.filtered_list_store.set_visible_func(self.list_store_visible_func)
			self.tree_view = gtk.TreeView(self.sorted_list_store)

			# create the view
			column_title = gtk.TreeViewColumn()#"Title",gtk.CellRendererText(),text=0)
			column_title.set_title(_("Title"))
			renderer = gtk.CellRendererPixbuf()
			column_title.pack_start(renderer, expand=False)
			column_title.set_cell_data_func(renderer,self.model_data_func,"image")
			renderer = gtk.CellRendererText()
			column_title.pack_start(renderer, expand=True)
			column_title.add_attribute(renderer, 'text', 0)
			column_title.set_resizable(True)
			column_title.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
			column_title.set_fixed_width(100)
			column_title.set_expand(True)
			self.tree_view.append_column(column_title)

			"""column_genre = gtk.TreeViewColumn("Tags",gtk.CellRendererText(),text=1)
			column_genre.set_resizable(True)
			column_genre.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
			column_genre.set_fixed_width(100)
			self.tree_view.append_column(column_genre)

			column_bitrate = gtk.TreeViewColumn("Bitrate",gtk.CellRendererText(),text=2)
			column_bitrate.set_resizable(True)
			column_bitrate.set_sizing(gtk.TREE_VIEW_COLUMN_FIXED)
			column_bitrate.set_fixed_width(100)
			self.tree_view.append_column(column_bitrate)"""

			self.info_box_tree = gtk.HBox()

			# add some more listeners for tree view...
			# - row double click
			self.tree_view.connect("row-activated",self.row_activated_handler)
			# - selection change
			self.tree_view.connect("cursor-changed",self.treeview_cursor_changed_handler,self.info_box_tree)

			# create icon view
			self.icon_view = gtk.IconView()
			self.icon_view.set_text_column(0)
			self.icon_view.set_pixbuf_column(2)
			self.icon_view.set_item_width(150)
			self.icon_view.set_selection_mode(gtk.SELECTION_SINGLE)
			self.icon_view.connect("item-activated", self.on_item_activated_icon_view)
			self.icon_view.connect("selection-changed", self.on_selection_changed_icon_view)

			self.tree_view_container = gtk.ScrolledWindow()
			self.tree_view_container.set_shadow_type(gtk.SHADOW_IN)
			self.tree_view_container.add(self.tree_view)
			self.tree_view_container.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)

			self.icon_view_container = gtk.ScrolledWindow()
			self.icon_view_container.set_shadow_type(gtk.SHADOW_IN)
			self.icon_view_container.add(self.icon_view)
			self.icon_view_container.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)

			self.view = gtk.HBox()
			self.view.pack_start(self.tree_view_container)
			self.view.pack_start(self.icon_view_container)

			filterbox = gtk.HBox()
			filterbox.pack_start(gtk.Label(_("Filter")+":"),False)
			filterbox.pack_start(self.filter_entry)
			filterbox.pack_start(gtk.Label(_("Genre")+":"),False)
			filterbox.pack_start(self.filter_entry_genre,False)
			filterbox.pack_start(gtk.Label(_("Bitrate")+":"),False)
			filterbox.pack_start(self.filter_entry_bitrate,False)

			self.start_box = gtk.HPaned()

			# prepare search tab
			self.info_box_search = gtk.HBox()
			self.search_box = gtk.VBox()
			self.search_entry = gtk.Entry()
			#self.search_entry.connect("changed",self.filter_entry_changed)

			def searchButtonClick(widget):
				self.doSearch(self.search_entry.get_text())
			searchbutton = gtk.Button(_("Search"))
			searchbutton.connect("clicked",searchButtonClick)

			search_input_box = gtk.HBox()
			search_input_box.pack_start(self.search_entry)
			search_input_box.pack_start(searchbutton,False)

			self.result_box = gtk.TreeView()
			self.result_box.connect("row-activated",self.row_activated_handler)
			self.result_box.connect("cursor-changed",self.treeview_cursor_changed_handler,self.info_box_search)

			self.result_box_container = gtk.ScrolledWindow()
			self.result_box_container.set_shadow_type(gtk.SHADOW_IN)
			self.result_box_container.add(self.result_box)
			self.result_box_container.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)
			self.result_box.append_column(gtk.TreeViewColumn(_("Title"),gtk.CellRendererText(),text=0))

			self.search_box.pack_start(search_input_box,False)
			self.search_box.pack_start(self.result_box_container)
			self.search_box.pack_start(self.info_box_search,False)

			stations_box = gtk.VBox()
			stations_box.pack_start(filterbox,False)
			stations_box.pack_start(self.view)
			stations_box.pack_start(self.info_box_tree,False)

			self.notebook = gtk.Notebook()
			self.notebook.append_page(self.start_box,gtk.Label(_("Favourites")))
			self.notebook.append_page(self.search_box,gtk.Label(_("Search")))
			self.notebook.append_page(stations_box,gtk.Label(_("Radiostation list")))
			self.notebook.set_scrollable(True)
			self.notebook.connect("switch-page",self.event_page_switch)
			
			self.pack_start(self.notebook)
			self.notebook.show_all()
			self.icon_view_container.hide_all()

			# initialize lists for recording streams and icon cache
			self.recording_streams = {}
			self.icon_cache = {}

			# start icon downloader thread
			# use queue for communication with thread
			# enqueued addresses will get downloaded
			self.icon_download_queue = Queue.Queue()
			self.icon_download_thread = threading.Thread(target = self.icon_download_worker)
			self.icon_download_thread.setDaemon(True)
			self.icon_download_thread.start()

			# first time filling of the model
			self.main_list_filled = False
			
			# enable images on buttons
			settings = gtk.settings_get_default()
			settings.set_property("gtk-button-images",True)

		rb.BrowserSource.do_impl_activate (self)

	def searchEngines(self):
		yield FeedLocal(self.cache_dir,self.update_download_status)
		yield FeedIcecast(self.cache_dir,self.update_download_status)
		yield FeedBoard(self.cache_dir,self.update_download_status)
		yield FeedShoutcast(self.cache_dir,self.update_download_status)
		yield FeedRadioTime(self.cache_dir,self.update_download_status)

	def doSearch(self, term):
		search_model = gtk.ListStore(str)
		search_model.append((_("Searching for : '%s'") % term,))

		# unset model
		self.result_box.set_model(search_model)
		# start thread
		search_thread = threading.Thread(target = self.doSearchThread, args = (term,))
		search_thread.start()

	def doSearchThread(self,term):
		results = {}
		self.station_actions = {}

		# check each engine for search method
		for feed in self.searchEngines():
			try:
				feed.search
			except:
				print "no search support in : "+feed.name()
				continue

			# call search method
			try:
				self.station_actions[feed.name()] = feed.get_station_actions()
				result = feed.search(term)
				results[feed.name()] = result
			except Exception,e:
				print "error with source:"+feed.name()
				print "error:"+str(e)

		gtk.gdk.threads_enter()
		# create new model
		new_model = gtk.TreeStore(str,object)
		# add entries to model
		for name in results.keys():
			result = results[name]
			source_parent = new_model.append(None,(name+" ("+str(len(result))+")",None))
			for entry in result:
				new_model.append(source_parent,(entry.server_name,entry))

		# set model of result_box
		new_model.set_sort_column_id(0,gtk.SORT_ASCENDING)
		self.result_box.set_model(new_model)
		gtk.gdk.threads_leave()
			
	def download_click_statistic(self):
		# download statistics
		statisticsStr = ""
		try:
			remotefile = urllib2.urlopen("http://www.radio-browser.info/topclick.php?limit=10")
			statisticsStr = remotefile.read()
		
		except Exception, e:
			print "download failed exception"
			print e
			return
		
		# parse statistics
		self.statistics_handler = BoardHandler()
		xml.sax.parseString(statisticsStr,self.statistics_handler)
	
		# fill statistics box
		self.refill_statistics(thread=True)
	
	def shortStr(self,longstring,maxlen):
		if len(longstring) > maxlen:
			short_value = longstring[0:maxlen-3]+"..."
		else:
			short_value = longstring
		return short_value
	
	def refill_statistics(self, thread=False):
		# check if already downloaded
		try:
			self.statistics_handler
		except:
			transmit_thread = threading.Thread(target = self.download_click_statistic)
			transmit_thread.start()
			return
		
		def button_click(widget,name,station):
			self.play_uri(station)
		
		def button_add_click(widget,name,station):
			data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
			if data is None:
				data = {}
			if station.server_name not in data:
				data[station.server_name] = station
			self.save_to_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME),data)
			
			self.refill_favourites()
		
		if thread:
			gtk.gdk.threads_enter()
		for entry in self.statistics_handler.entries:
			button = gtk.Button(self.shortStr(entry.server_name,30)+" ("+entry.clickcount+")")
			button.connect("clicked",button_click,entry.server_name,entry)
			
			button_add = gtk.Button()
			img = gtk.Image()
			img.set_from_stock(gtk.STOCK_GO_FORWARD,gtk.ICON_SIZE_BUTTON)
			button_add.set_image(img)
			button_add.connect("clicked",button_add_click,entry.server_name,entry)
			line = gtk.HBox()
			line.pack_start(button)
			line.pack_start(button_add,expand=False)
			
			self.statistics_box.pack_start(line,expand=False)
			line.show_all()
			
		self.statistics_box_parent.show_all()
		if thread:
			gtk.gdk.threads_leave()

	def refill_favourites(self):
		print "refill favourites"
		width, height = gtk.icon_size_lookup(gtk.ICON_SIZE_BUTTON)
		
		# remove all old information in infobox
		for widget in self.start_box.get_children():
			self.start_box.remove(widget)
		
		def button_click(widget,name,station):
			self.play_uri(station)
		
		def button_record_click(widget,name,station):
			self.record_uri(station)
		
		def button_add_click(widget,name,station):
			data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
			if data is None:
				data = {}
			if station.server_name not in data:
				data[station.server_name] = station
			self.save_to_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME),data)
			
			self.refill_favourites()
		
		def button_delete_click(widget,name,station):
			data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
			if data is None:
				data = {}
			if station.server_name in data:
				del data[station.server_name]
			self.save_to_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME),data)
			
			self.refill_favourites()
		
		left_box = gtk.VBox()
		left_box.show()
		
		# add click statistics list
		self.statistics_box = gtk.VBox()
		scrolled_box = gtk.ScrolledWindow()
		scrolled_box.add_with_viewport(self.statistics_box)
		scrolled_box.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)
		decorated_box = gtk.Frame(_("Click statistics (Last 30 days)"))
		decorated_box.add(scrolled_box)
		self.statistics_box_parent = decorated_box
		left_box.pack_start(decorated_box)
		
		self.refill_statistics()
		
		# add recently played list
		recently_box = gtk.VBox()
		scrolled_box = gtk.ScrolledWindow()
		scrolled_box.add_with_viewport(recently_box)
		scrolled_box.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)
		decorated_box = gtk.Frame(_("Recently played"))
		decorated_box.add(scrolled_box)
		left_box.pack_start(decorated_box)
		
		self.start_box.pack1(left_box)
		
		data = self.load_from_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME))
		if data is None:
			data = {}
		dataNew = {}
		sortedkeys = sorted(data.keys())
		for name in sortedkeys:
			station = data[name]
			if datetime.datetime.now()-station.PlayTime <= datetime.timedelta(days=float(self.plugin.recently_played_purge_days)):
				if len(name) > 53:
					short_value = name[0:50]+"..."
				else:
					short_value = name
				button = gtk.Button(short_value)
				button.connect("clicked",button_click,name,station)
				
				button_add = gtk.Button()
				img = gtk.Image()
				img.set_from_stock(gtk.STOCK_GO_FORWARD,gtk.ICON_SIZE_BUTTON)
				button_add.set_image(img)
				button_add.connect("clicked",button_add_click,name,station)
				line = gtk.HBox()
				line.pack_start(button)
				line.pack_start(button_add,expand=False)
				
				recently_box.pack_start(line, expand=False)
				dataNew[name] = station
				
				try:
					if station.icon_src != "":
						hash_src = hashlib.md5(station.icon_src).hexdigest()
						filepath = os.path.join(self.icon_cache_dir, hash_src)
						if os.path.exists(filepath):
							buffer = gtk.gdk.pixbuf_new_from_file_at_size(filepath,width,height)
							img = gtk.Image()
							img.set_from_pixbuf(buffer)
							img.show()
							button.set_image(img)
				except:
					print "could not set image for station:"+str(station.server_name)
		
		if len(sortedkeys)>0:
			decorated_box.show_all()
		self.save_to_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME),dataNew)

		# add bookmarks
		favourites_box = gtk.VBox()
		scrolled_box = gtk.ScrolledWindow()
		scrolled_box.add_with_viewport(favourites_box)
		scrolled_box.set_property("hscrollbar-policy", gtk.POLICY_AUTOMATIC)
		decorated_box = gtk.Frame(_("Favourites"))
		decorated_box.add(scrolled_box)
		self.start_box.pack2(decorated_box)
		
		data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
		if data is None:
			data = {}
		sortedkeys = sorted(data.keys())
		for name in sortedkeys:
			line = gtk.HBox()
			station = data[name]
			if len(name) > 53:
				short_value = name[0:50]+"..."
			else:
				short_value = name
			
			button = gtk.Button(short_value)
			button.connect("clicked",button_click,name,station)
			button_delete = gtk.Button()
			img = gtk.Image()
			img.set_from_stock(gtk.STOCK_DELETE,gtk.ICON_SIZE_BUTTON)
			button_delete.set_image(img)
			button_delete.connect("clicked",button_delete_click,name,station)
			
			button_record = gtk.Button()
			img = gtk.Image()
			img.set_from_stock(gtk.STOCK_MEDIA_RECORD,gtk.ICON_SIZE_BUTTON)
			button_record.set_image(img)
			button_record.connect("clicked",button_record_click,name,station)
			
			line.pack_start(button)
			line.pack_start(button_record,expand=False)
			line.pack_start(button_delete,expand=False)
			favourites_box.pack_start(line, expand=False)
			
			try:
				if station.icon_src != "":
					hash_src = hashlib.md5(station.icon_src).hexdigest()
					filepath = os.path.join(self.icon_cache_dir, hash_src)
					if os.path.exists(filepath):
						buffer = gtk.gdk.pixbuf_new_from_file_at_size(filepath,width,height)
						img = gtk.Image()
						img.set_from_pixbuf(buffer)
						img.show()
						button.set_image(img)
			except:
				print "could not set image for station:"+str(station.server_name)
		if (len(sortedkeys) > 0):
			decorated_box.show_all()

	""" handler for page switches in the main notebook """
	def event_page_switch(self,notebook,page,page_num):
		if page_num == 0:
			# update favourites each time user selects it
			self.refill_favourites()
		if page_num == 1:
			pass
		if page_num == 2:
			if not self.main_list_filled:
				# fill the list only the first time, the user selects the main tab
				self.main_list_filled = True
				self.refill_list()

	""" listener on double click in search view """
	def on_item_activated_icon_view(self,widget,item):
		model = widget.get_model()
		station = model[item][1]

		self.play_uri(station)

	""" listener on selection change in search view """
	def on_selection_changed_icon_view(self,widget):
		model = widget.get_model()
		items = widget.get_selected_items()

		if len(items) == 1:
			obj = model[items[0]][1]
			self.update_info_box(obj)
	
	""" listener for selection changes """
	def treeview_cursor_changed_handler(self,treeview,info_box):
		# get selected item
		selection = treeview.get_selection()
		model,iter = selection.get_selected()

		# if some item is selected
		if not iter == None:
			obj = model.get_value(iter,1)
			self.update_info_box(obj,info_box)

	def update_info_box(self,obj,info_box):
		# remove all old information in infobox
		for widget in info_box.get_children():
			info_box.remove(widget)

		# create new infobox
		info_container = gtk.Table(12,2)
		info_container.set_col_spacing(0,10)
		self.info_box_added_rows = 0

		# convinience method for adding new labels to infobox
		def add_label(title,value,shorten=True):
			if value == None:
				return
			if not value == "":
				if shorten:
					if len(value) > 53:
						short_value = value[0:50]+"..."
					else:
						short_value = value
				else:
					short_value = value

				label = gtk.Label()
				label.set_line_wrap(True)
				if value.startswith("http://") or value.startswith("mms:") or value.startswith("mailto:"):
					label.set_markup("<a href='"+xml.sax.saxutils.escape(value)+"'>"+xml.sax.saxutils.escape(short_value)+"</a>")
				else:
					label.set_markup(xml.sax.saxutils.escape(short_value))
				label.set_selectable(True)
				label.set_alignment(0, 0)

				title_label = gtk.Label(title)
				title_label.set_alignment(1, 0)
				title_label.set_markup("<b>"+xml.sax.saxutils.escape(title)+"</b>")
				info_container.attach(title_label,0,1,self.info_box_added_rows,self.info_box_added_rows+1)
				info_container.attach(label,1,2,self.info_box_added_rows,self.info_box_added_rows+1)
				self.info_box_added_rows = self.info_box_added_rows+1

		if isinstance(obj,Feed):
			feed = obj
			add_label(_("Entry type"),_("Feed"))

			add_label(_("Description"),feed.getDescription(),False)
			add_label(_("Feed homepage"),feed.getHomepage())
			add_label(_("Feed source"),feed.getSource())

			try:
				t = os.path.getmtime(feed.filename)
				timestr = datetime.datetime.fromtimestamp(t).strftime("%x %X")
			except:
				timestr = _("No local copy")
			add_label(_("Last update"),timestr)

		if isinstance(obj,RadioStation):
			station = obj
			add_label(_("Source feed"),station.type)
			add_label(_("Name"),station.server_name)
			add_label(_("Tags"),station.genre)
			add_label(_("Bitrate"),station.bitrate)
			add_label(_("Server type"),station.server_type)
			add_label(_("Homepage"),station.homepage)
			add_label(_("Current song (on last refresh)"),station.current_song)
			add_label(_("Current listeners"),station.listeners)
			add_label(_("Language"),station.language)
			add_label(_("Country"),station.country)
			add_label(_("Votes"),station.votes)
			add_label(_("Negative votes"),station.negativevotes)
			add_label(_("Stream URL"),station.listen_url)
			try:
				PlayTime = station.PlayTime.strftime("%x %X")
				add_label(_("Added to recently played at"),PlayTime)
			except:
				pass

		button_box = gtk.VBox()

		def button_play_handler(widget,station):
			self.play_uri(station)

		def button_bookmark_handler(widget,station):
			data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
			if data is None:
				data = {}
			if station.server_name not in data:
				self.tree_store.append(self.bookmarks_iter,(station.server_name,station))
				data[station.server_name] = station
				widget.set_label(_("Unbookmark"))
			else:
				iter = self.tree_store.iter_children(self.bookmarks_iter)
				while True:
					title = self.tree_store.get_value(iter,0)

					if title == station.server_name:
						self.tree_store.remove(iter)
						break

					iter = self.tree_store.iter_next(iter)
					if iter == None:
						break
				del data[station.server_name]
				widget.set_label(_("Bookmark"))
			self.save_to_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME),data)

		def button_record_handler(widget,station):
			self.record_uri(station)

		def button_download_handler(widget,feed):
			transmit_thread = threading.Thread(target = self.download_feed,args = (feed,))
			transmit_thread.setDaemon(True)
			transmit_thread.start()

		def button_action_handler(widget,action):
			action.call(self)

		def button_station_action_handler(widget,action,station):
			action.call(self,station)

		if isinstance(obj,Feed):
			feed = obj
			if os.path.isfile(feed.filename):
				button = gtk.Button(_("Redownload"))
				button.connect("clicked", button_download_handler, feed)
			else:
				button = gtk.Button(_("Download"))
				button.connect("clicked", button_download_handler, feed)
			button_box.pack_start(button,False)

			for action in feed.get_feed_actions():
				button = gtk.Button(action.name)
				button.connect("clicked",button_action_handler,action)
				button_box.pack_start(button,False)

		if isinstance(obj,RadioStation):
			button = gtk.Button(_("Play"))
			button.connect("clicked", button_play_handler, obj)
			button_box.pack_start(button,False)

			# check for streamripper, before displaying record button
			try:
				process = subprocess.Popen("streamripper",stdout=subprocess.PIPE)
				process.communicate()
				process.wait()
			except(OSError):
				print "streamripper not found"
			else:
				button = gtk.Button(_("Record"))
				button.connect("clicked", button_record_handler, obj)
				button_box.pack_start(button,False)

			data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
			if data is None:
				data = {}
			if station.server_name not in data:
				button = gtk.Button(_("Bookmark"))
			else:
				button = gtk.Button(_("Unbookmark"))
			button.connect("clicked", button_bookmark_handler, obj)
			button_box.pack_start(button,False)

			if station.type in self.station_actions.keys():
				actions = self.station_actions[station.type]
				for action in actions:
					button = gtk.Button(action.name)
					button.connect("clicked", button_station_action_handler, action,obj)
					button_box.pack_start(button,False)

		sub_info_box = gtk.HBox()
		sub_info_box.pack_start(info_container)
		sub_info_box.pack_start(button_box,False)

		decorated_info_box = gtk.Frame(_("Info box"))
		decorated_info_box.add(sub_info_box)

		info_box.pack_start(decorated_info_box)
		info_box.show_all()

	""" icon download worker thread function """
	def icon_download_worker(self):
		while True:
			filepath,src = self.icon_download_queue.get()

			if os.path.exists(filepath) is False:
				if src.lower().startswith("http://"):
					try:
						urllib.urlretrieve(src,filepath)
					except:
						pass

			self.icon_download_queue.task_done()

	""" tries to load icon from disk and if found it saves it in cache returns it """
	def get_icon_pixbuf(self,filepath,return_value_not_found=None):
		if os.path.exists(filepath):
			width, height = gtk.icon_size_lookup(gtk.ICON_SIZE_BUTTON)
			if filepath in self.icon_cache:
				return self.icon_cache[filepath]
			else:
				try:
					icon = gtk.gdk.pixbuf_new_from_file_at_size(filepath,width,height)
				except:
					icon = return_value_not_found
				self.icon_cache[filepath] = icon
			return icon
		return return_value_not_found

	""" data display function for tree view """
	def model_data_func(self,column,cell,model,iter,infostr):
		obj = model.get_value(iter,1)
		self.clef_icon = self.get_icon_pixbuf(self.plugin.find_file("note.png"))

		if infostr == "image":
			icon = None

			if isinstance(obj,RadioStation):
				station = obj
				# default icon
				icon = self.clef_icon

				# icons for special feeds
				if station.type == "Shoutcast":
					icon = self.get_icon_pixbuf(self.plugin.find_file("shoutcast-logo.png"))
				if station.type == "Icecast":
					icon = self.get_icon_pixbuf(self.plugin.find_file("xiph-logo.png"))
				if station.type == "Local":
					icon = self.get_icon_pixbuf(self.plugin.find_file("local-logo.png"))

				# most special icons, if the station has one for itsself
				if station.icon_src != "":
					hash_src = hashlib.md5(station.icon_src).hexdigest()
					filepath = os.path.join(self.icon_cache_dir, hash_src)
					if os.path.exists(filepath):
						icon = self.get_icon_pixbuf(filepath,icon)
					else:
						# load icon
						self.icon_download_queue.put([filepath,station.icon_src])

			if icon is None:
				cell.set_property("stock-id",gtk.STOCK_DIRECTORY)
			else:
				cell.set_property("pixbuf",icon)

	""" transmits station information to board """
	def transmit_station(self,station):
		params = urllib.urlencode({'action':'clicked','name': station.server_name,'url': station.getRealURL(),'source':station.type})
		f = urllib.urlopen(BOARD_ROOT+"?%s" % params)
		f.read()
		print "Transmit station '"+str(station.server_name)+"' OK"

	""" transmits title information to board """
	"""def transmit_title(self,title):
		params = urllib.urlencode({'action':'streaming','name': self.station.server_name,'url': self.station.getRealURL(),'source':self.station.type,'title':title})
		f = urllib.urlopen(BOARD_ROOT+"?%s" % params)
		f.read()
		print "Transmit title '"+str(title)+"' OK"
	"""
	""" stream information listener """
	def info_available(self,player,uri,field,value):
		if field == RB_METADATA_FIELD_TITLE:
			self.title = value
			self.set_streaming_title(self.title)
			#transmit_thread = threading.Thread(target = self.transmit_title,args = (value,))
			#transmit_thread.setDaemon(True)
			#transmit_thread.start()
			#print "setting title to:"+value
           
		elif field == RB_METADATA_FIELD_GENRE:
			self.genre = value
			## causes warning: RhythmDB-WARNING **: trying to sync properties of non-editable file
			#self.shell.props.db.set(self.entry, rhythmdb.PROP_GENRE, value)
			#self.shell.props.db.commit()
			#print "setting genre to:"+value

		elif field == RB_METADATA_FIELD_BITRATE:
			## causes warning: RhythmDB-WARNING **: trying to sync properties of non-editable file
			#self.shell.props.db.set(self.entry, rhythmdb.PROP_BITRATE, value/1000)
			#self.shell.props.db.commit()
			#print "setting bitrate to:"+str(value/1000)
			pass

		else:
			print "Server sent unknown info '"+str(field)+"':'"+str(value)+"'"

#	def playing_changed (self, sp, playing):
#		print "playing changed"

#	def playing_entry_changed (self, sp, entry):
#		print "playing entry changed"

#	def playing_song_property_changed (self, sp, uri, property, old, new):
#		print "property changed "+str(new)

	def record_uri(self,station):
		play_thread = threading.Thread(target = self.play_uri_,args = (station,True))
		play_thread.setDaemon(True)
		play_thread.start()

	""" listener for filter entry change """
	def filter_entry_changed(self,gtk_entry):
		if self.filter_entry.get_text() == "" and self.filter_entry_genre.get_text() == "":
			self.tree_view_container.show_all()
			self.icon_view_container.hide_all()
		else:
			self.tree_view_container.hide_all()
			self.icon_view_container.show_all()

		self.icon_view.set_model()
		self.filtered_icon_view_store.refilter()
		self.icon_view.set_model(self.filtered_icon_view_store)
		self.notify_status_changed()

	""" callback for item filtering """
	def list_store_visible_func(self,model,iter):
		# returns true if the row should be visible
		if len(model) == 0:
			return True
		obj = model.get_value(iter,1)
		if isinstance(obj,RadioStation):
			station = obj
			try:
				bitrate = int(station.bitrate)
				min_bitrate = int(float(self.filter_entry_bitrate.get_value()))
				if bitrate < min_bitrate:
					return False
			except:
				pass

			filter_string = self.filter_entry.get_text().lower()
			if filter_string != "":
				if station.server_name.lower().find(filter_string) < 0:
					return False

			filter_string = self.filter_entry_genre.get_text().lower()
			if filter_string != "":
				genre = station.genre
				if genre is None:
					genre = ""
				if genre.lower().find(filter_string) < 0:
					return False

			return True
		else:
			return True

	""" handler for update toolbar button """
	def update_button_clicked(self):
		if not self.updating:
			# delete cache files
			files = os.listdir(self.cache_dir)
			for filename in files:
				if filename.endswith("xml"):
					filepath = os.path.join(self.cache_dir, filename)
					os.unlink(filepath)
			# start filling again
			self.refill_list()

	def clear_iconcache_button_clicked(self):
		if not self.updating:
			# delete cache files
			files = os.listdir(self.icon_cache_dir)
			for filename in files:
				filepath = os.path.join(self.icon_cache_dir, filename)
				os.unlink(filepath)
			# delete internal cache
			self.icon_cache = {}
			# start filling again
			self.refill_list()
		pass

	""" starts playback of the station """
	def play_uri(self,station):
		station.updateRealURL()
		play_thread = threading.Thread(target = self.play_uri_,args = (station,))
		play_thread.setDaemon(True)
		play_thread.start()

	def play_uri_(self,station,record=False):
		# do not play while downloading
		if self.updating:
			return

		# try downloading station information
		tryno = 0
		self.updating = True
		while True:
			tryno += 1

			gtk.gdk.threads_enter()
			self.load_status = _("downloading station information")+" '"+station.server_name+"', "+_("Try")+":"+str(tryno)+"/"+str(math.floor(float(self.plugin.download_trys)))
			self.load_total_size = 0
			self.notify_status_changed()
			gtk.gdk.threads_leave()

			if station.getRealURL() is not None:
				break
			if tryno >= float(self.plugin.download_trys):
				gtk.gdk.threads_enter()
				self.load_status = ""
				self.updating = False
				self.notify_status_changed()
				message = gtk.MessageDialog(message_format=_("Could not download station information"),buttons=gtk.BUTTONS_OK,type=gtk.MESSAGE_ERROR)
				message.format_secondary_text(_("Could not download station information from shoutcast directory server. Please try again later."))
				response = message.run()
				message.destroy()
				gtk.gdk.threads_leave()
				return

		if not station.listen_url.startswith("http://127.0.0.1"):
			# add to recently played
			data = self.load_from_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME))
			if data is None:
				data = {}
			if station.server_name not in data:
				try:
					self.tree_store.append(self.recently_iter,(station.server_name,station))
				except:
					pass
				data[station.server_name] = station
				data[station.server_name].PlayTime = datetime.datetime.now()
			else:
				data[station.server_name].PlayTime = datetime.datetime.now()
			
			self.save_to_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME),data)

		gtk.gdk.threads_enter()
		self.load_status = ""
		self.updating = False
		self.notify_status_changed()

		if record:
			def short_name(name):
				maxlen = 30
				if len(name) > maxlen:
					return name[0:maxlen-3]+"..."
				else:
					return name

			uri = station.getRealURL()

			# do not record the same stream twice
			if uri in self.recording_streams:
				if self.recording_streams[uri].process.poll() is None:
					return
			self.recording_streams[uri] = RecordProcess(station,self.plugin.outputpath,self.play_uri,self.shell)
			self.notebook.append_page(self.recording_streams[uri],gtk.Label(short_name(station.server_name)))
			self.recording_streams[uri].start()
			self.notebook.set_current_page(self.notebook.page_num(self.recording_streams[uri]))
		else:
			# get player
			player = self.shell.get_player()
			player.stop()

			# create new entry to play
			self.entry = self.shell.props.db.entry_lookup_by_location(station.getRealURL())
			if self.entry == None:
				#self.shell.props.db.entry_delete(self.entry)

				self.entry = self.shell.props.db.entry_new(self.entry_type, station.getRealURL())
				self.shell.props.db.set(self.entry, rhythmdb.PROP_TITLE, station.server_name)
				self.shell.props.db.commit()
			#shell.load_uri(uri,False)

			# start playback
			player.play_entry(self.entry,self)

		gtk.gdk.threads_leave()

		# transmit station click to station board (statistic) """
		transmit_thread = threading.Thread(target = self.transmit_station,args = (station,))
		transmit_thread.setDaemon(True)
		transmit_thread.start()

	""" handler for double clicks in tree view """
	def row_activated_handler(self,treeview,path,column):
		model = treeview.get_model()
		myiter = model.get_iter(path)
		
		obj = model.get_value(myiter,1)
		if obj == None:
			return

		if isinstance(obj,RadioStation):
			station = obj
			if station is not None:
				self.play_uri(station)

		if isinstance(obj,Feed):
			feed = obj
			transmit_thread = threading.Thread(target = self.download_feed,args = (feed,))
			transmit_thread.setDaemon(True)
			transmit_thread.start()

	def download_feed(self,feed):
		tryno = 0
		self.updating = True
		while True:
			tryno += 1

			gtk.gdk.threads_enter()
			self.load_status = _("Downloading feed %(name)s from %(url)s. %(try)d/%(trys)d") % {'name':feed.name(), 'url':feed.uri, 'try':tryno, 'trys':(math.floor(float(self.plugin.download_trys)))}
			self.load_total_size = 0
			self.notify_status_changed()
			gtk.gdk.threads_leave()

			if feed.download():
				break

			if tryno >= float(self.plugin.download_trys):
				gtk.gdk.threads_enter()
				self.load_status = ""
				self.updating = False
				self.notify_status_changed()
				message = gtk.MessageDialog(message_format=_("Feed download failed"),buttons=gtk.BUTTONS_OK,type=gtk.MESSAGE_ERROR)
				message.format_secondary_text(_("Could not download feed. Please try again later."))
				response = message.run()
				message.destroy()
				gtk.gdk.threads_leave()
				return

		self.refill_list()

	def do_impl_delete_thyself(self):
		if self.hasActivated:
			# kill all running records
			for uri in self.recording_streams.keys():
				self.recording_streams[uri].stop()
			self.shell = False

	def engines(self):
		yield FeedLocal(self.cache_dir,self.update_download_status)
		yield FeedIcecast(self.cache_dir,self.update_download_status)
		yield FeedBoard(self.cache_dir,self.update_download_status)
		yield FeedShoutcast(self.cache_dir,self.update_download_status)
		yield FeedRadioTime(self.cache_dir,self.update_download_status)
		yield FeedRadioTimeLocal(self.cache_dir,self.update_download_status)

	def get_stock_icon(self, name):
		theme = gtk.icon_theme_get_default()
		return theme.load_icon(name, 48, 0)

	def load_icon_file(self,filepath,value_not_found):
		icon = value_not_found
		try:
			icon = gtk.gdk.pixbuf_new_from_file_at_size(filepath,72,72)
		except:
			icon = value_not_found
		return icon

	def get_station_icon(self,station,default_icon):
		# default icon
		icon = default_icon

		# most special icons, if the station has one for itsself
		if station.icon_src != "":
			if station.icon_src is not None:
				hash_src = hashlib.md5(station.icon_src).hexdigest()
				filepath = os.path.join(self.icon_cache_dir, hash_src)
				if os.path.exists(filepath):
					icon = self.load_icon_file(filepath,icon)
				else:
					# load icon
					self.icon_download_queue.put([filepath,station.icon_src])
		return icon

	def insert_feed(self,feed,parent):
		# preload most used icons
		note_icon = self.load_icon_file(self.plugin.find_file("note.png"),None)
		shoutcast_icon = self.load_icon_file(self.plugin.find_file("shoutcast-logo.png"),None)
		xiph_icon = self.load_icon_file(self.plugin.find_file("xiph-logo.png"),None)
		local_icon = self.load_icon_file(self.plugin.find_file("local-logo.png"),None)

		gtk.gdk.threads_enter()
		self.load_status = _("Loading feed %(name)s") % {'name':feed.name(),}
		self.load_total_size = 0
		self.notify_status_changed()
		gtk.gdk.threads_leave()

		# create main feed root item
		current_iter = self.tree_store.append(parent,(feed.name(),feed))

		# initialize dicts for iters
		genres = {}
		countries = {}
		subcountries = {}
		streamtypes = {}
		bitrates = {}

		# load entries
		entries = feed.entries()

		gtk.gdk.threads_enter()
		self.load_status = _("Integrating feed %(name)s (%(itemcount)d items) into tree...") % {'name':feed.name(),'itemcount':len(entries)}
		self.notify_status_changed()
		gtk.gdk.threads_leave()

		def short_name(name):
			maxlen = 50
			if len(name) > maxlen:
				return name[0:maxlen-3]+"..."
			else:
				return name

		self.load_total_size = len(entries)
		self.load_current_size = 0

		stations_count = 0

		for obj in entries:
			if isinstance(obj,Feed):
				sub_feed = obj
				# add sub feed to treeview
				stations_count += self.insert_feed(sub_feed,current_iter)

			elif isinstance(obj,RadioStation):
				stations_count += 1
				station = obj
				# add subitems for sorting, if there are stations
				if self.load_current_size == 0:
					genre_iter = self.tree_store.append(current_iter,(_("By Genres"),None))
					country_iter = self.tree_store.append(current_iter,(_("By Country"),None))
					streamtype_iter = self.tree_store.append(current_iter,(_("By Streamtype"),None))
					bitrate_iter = self.tree_store.append(current_iter,(_("By Bitrate"),None))

				# display status info in statusbar
				self.load_current_size += 1
				gtk.gdk.threads_enter()
				if self.load_current_size % 50 == 0:
					self.notify_status_changed()
				gtk.gdk.threads_leave()

				# default icon
				icon = note_icon
				# icons for special feeds
				if station.type == "Shoutcast":
					icon = shoutcast_icon
				if station.type == "Icecast":
					icon = xiph_icon
				if station.type == "Local":
					icon = local_icon

				# add new station to liststore of search-view too
				self.icon_view_store.append((short_name(station.server_name),station,self.get_station_icon(station,icon)))

				# add station to treeview, by streamtype
				if station.server_type not in streamtypes:
					streamtypes[station.server_type] = self.tree_store.append(streamtype_iter,(station.server_type,None))
				self.tree_store.append(streamtypes[station.server_type],(station.server_name,station))

				# add station to treeview, by bitrate
				br = station.bitrate
				try:
					br_int = int(br)
					br = str((((br_int-1)/32)+1)*32)
					if br_int > 512:
						br = _("Invalid")
				except:
					pass
				if br not in bitrates:
					bitrates[br] = self.tree_store.append(bitrate_iter,(br,None))
				self.tree_store.append(bitrates[br],(station.server_name,station))

				# add station to treeview, by genre
				if station.genre is not None:
					for genre in station.genre.split(","):
						genre = genre.strip().lower()
						if genre not in genres:
							genres[genre] = self.tree_store.append(genre_iter,(genre,None))
						self.genre_list[genre] = 1
						self.tree_store.append(genres[genre],(station.server_name,station))

				# add station to treeview, by country
				country_arr = station.country.split("/")
				if country_arr[0] not in countries:
					countries[country_arr[0]] = self.tree_store.append(country_iter,(country_arr[0],None))
				if len(country_arr) == 2:
					if station.country not in subcountries:
						subcountries[station.country] = self.tree_store.append(countries[country_arr[0]],(country_arr[1],None))
					self.tree_store.append(subcountries[station.country],(station.server_name,station))
				else:
					self.tree_store.append(countries[country_arr[0]],(station.server_name,station))

			else:
				print "ERROR: unknown class type in feed"

		self.tree_store.set_value(current_iter,0,feed.name()+" ("+str(stations_count)+")")
		return stations_count

	def refill_list_worker(self):
		print "refill list worker"

		self.station_actions = {}

		self.tree_view.set_model()
		self.icon_view.set_model()
		#self.filter_entry_genre.set_model()

		self.updating = True
		# deactivate sorting
		self.icon_view_store = gtk.ListStore(str,object,gtk.gdk.Pixbuf)
		self.sorted_list_store.reset_default_sort_func()

		# delete old entries
		self.tree_store.clear()
		self.icon_view_store.clear()

		# add recently played list
		self.recently_iter = self.tree_store.append(None,(_("Recently played"),None))
		data = self.load_from_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME))
		if data is None:
			data = {}
		dataNew = {}
		for name,station in data.items():
			if datetime.datetime.now()-station.PlayTime <= datetime.timedelta(days=float(self.plugin.recently_played_purge_days)):
				self.tree_store.append(self.recently_iter,(name,station))
				dataNew[name] = station
		self.save_to_file(os.path.join(self.cache_dir,RECENTLY_USED_FILENAME),dataNew)

		# add bookmarks
		self.bookmarks_iter = self.tree_store.append(None,(_("Bookmarks"),None))
		data = self.load_from_file(os.path.join(self.cache_dir,BOOKMARKS_FILENAME))
		if data is None:
			data = {}
		for name,station in data.items():
			self.tree_store.append(self.bookmarks_iter,(name,station))

		# initialize genre dict for genre filter combobox
		self.genre_list = {}

		for feed in self.engines():
			try:
				self.station_actions[feed.name()] = feed.get_station_actions()
				self.insert_feed(feed,None)
			except Exception,e:
				print "error with source:"+feed.name()
				print "error:"+str(e)

		self.genre_liststore = gtk.ListStore(gobject.TYPE_STRING)
		self.genre_liststore.append(("",))
		for key in self.genre_list.keys():
			self.genre_liststore.append((key,))
		self.genre_liststore.set_sort_column_id(0,gtk.SORT_ASCENDING)
		completion = gtk.EntryCompletion()
		completion.set_text_column(0)
		completion.set_model(self.genre_liststore)
		self.filter_entry_genre.set_completion(completion)

		# activate sorting
		self.sorted_list_store.set_sort_column_id(0,gtk.SORT_ASCENDING)
		self.icon_view_store.set_sort_column_id(0,gtk.SORT_ASCENDING)

		# connect model to view
		self.filtered_icon_view_store = self.icon_view_store.filter_new()
		self.filtered_icon_view_store.set_visible_func(self.list_store_visible_func)

		self.tree_view.set_model(self.sorted_list_store)
		self.icon_view.set_model(self.filtered_icon_view_store)

		gtk.gdk.threads_enter()
		self.updating = False
		self.notify_status_changed()
		gtk.gdk.threads_leave()

	def refill_list(self):
		print "refill list"
		self.list_download_thread = threading.Thread(target = self.refill_list_worker)
		self.list_download_thread.setDaemon(True)
		self.list_download_thread.start()

	def load_from_file(self,filename):
		if not os.path.isfile(filename):
			return None

		try:
			f = open(filename,"r")
			p = pickle.Unpickler(f)
			data = p.load()
			f.close()
			return data
		except:
			print "load file did not work:"+filename
			return None

	def save_to_file(self,filename,obj):
		f = open(filename,"w")
		p = pickle.Pickler(f)
		p.dump(obj)
		f.close()
